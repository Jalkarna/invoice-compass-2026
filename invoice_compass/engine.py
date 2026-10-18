from __future__ import annotations

import csv
import io
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    client: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1200)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: Literal["INR", "USD", "EUR", "GBP"] = "INR"
    due_date: date
    status: Literal["unpaid", "paid", "draft"] = "unpaid"
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("client", "description")
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Must contain text")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 40 for v in values):
            raise ValueError("Tags must be nonempty strings of at most 40 characters")
        return [v.strip() for v in values]

    def search_text(self):
        return f"Client: {self.client}. Work: {self.description}. Topics: {', '.join(self.tags)}"


class Search(BaseModel):
    query: str = Field(min_length=1, max_length=250)
    status: Literal["all", "unpaid", "paid", "draft"] = "all"
    currency: Literal["all", "INR", "USD", "EUR", "GBP"] = "all"
    due_before: date | None = None
    overdue_only: bool = False
    as_of: date = Field(default_factory=date.today)
    min_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    min_score: float = Field(default=0.27, ge=0, le=1)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Enter a description of the work you are looking for")
        return value.strip()


class LocalEncoder:
    def __init__(self):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(
            model_name=MODEL,
            cache_dir=os.environ.get("INVOICE_COMPASS_MODEL_CACHE", ".cache/fastembed"),
            threads=2,
        )

    def encode(self, texts):
        matrix = np.asarray(list(self.model.embed(texts)), dtype=np.float32)
        return matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)


def totals(invoices, as_of):
    currencies = {}
    for invoice in invoices:
        row = currencies.setdefault(invoice.currency, {"unpaid": Decimal(0), "overdue": Decimal(0)})
        if invoice.status == "unpaid":
            row["unpaid"] += invoice.amount
            if invoice.due_date < as_of:
                row["overdue"] += invoice.amount
    return {key: {k: str(v.quantize(Decimal("0.01"))) for k, v in row.items()}
            for key, row in sorted(currencies.items())}


class Ledger:
    def __init__(self, invoices, encoder, storage=None):
        self.lock = RLock()
        self.encoder = encoder
        self.storage = Path(storage) if storage else None
        self.invoices = [Invoice.model_validate(i) for i in invoices]
        ids = [i.id for i in self.invoices]
        if len(ids) != len(set(ids)):
            raise ValueError("Invoice IDs must be unique")
        self.vectors = self.encoder.encode([i.search_text() for i in self.invoices])

    def list(self, as_of):
        with self.lock:
            return {"invoices": [i.model_dump(mode="json") for i in self.invoices],
                    "totals": totals(self.invoices, as_of), "as_of": as_of.isoformat()}

    def search(self, request: Search):
        with self.lock:
            query_vector = self.encoder.encode([request.query])[0]
            ranked = []
            for invoice, vector in zip(self.invoices, self.vectors, strict=True):
                if request.status != "all" and invoice.status != request.status:
                    continue
                if request.currency != "all" and invoice.currency != request.currency:
                    continue
                if request.due_before and invoice.due_date > request.due_before:
                    continue
                if request.min_amount is not None and invoice.amount < request.min_amount:
                    continue
                if request.overdue_only and not (
                    invoice.status == "unpaid" and invoice.due_date < request.as_of
                ):
                    continue
                score = float(np.clip(np.dot(query_vector, vector), -1, 1))
                if score >= request.min_score:
                    ranked.append((score, invoice))
            ranked.sort(key=lambda pair: (-pair[0], pair[1].id))
            selected = ranked[:request.limit]
            return {
                "query": request.query, "matched_count": len(ranked),
                "results": [{"invoice": invoice.model_dump(mode="json"), "similarity": round(score, 4)}
                            for score, invoice in selected],
                "totals": totals([i for _, i in ranked], request.as_of),
                "model": MODEL, "as_of": request.as_of.isoformat(),
                "notice": "Similarity is a ranking score, not a probability. Amounts and dates come directly from invoice records.",
            }

    def add(self, batch):
        validated = [Invoice.model_validate(row) for row in batch]
        if not 1 <= len(validated) <= 100:
            raise ValueError("Import between 1 and 100 invoices at a time")
        with self.lock:
            incoming = [i.id for i in validated]
            existing = {i.id for i in self.invoices}
            if len(incoming) != len(set(incoming)) or existing.intersection(incoming):
                raise ValueError("An invoice ID already exists or is repeated in this file; nothing was imported")
            if len(self.invoices) + len(validated) > 1000:
                raise ValueError("This workspace supports up to 1,000 invoices")
            vectors = self.encoder.encode([i.search_text() for i in validated])
            merged = self.invoices + validated
            if self.storage:
                self.storage.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.storage.with_suffix(".tmp")
                temporary.write_text(json.dumps([i.model_dump(mode="json") for i in merged], indent=2))
                temporary.chmod(0o600)
                temporary.replace(self.storage)
            self.invoices = merged
            self.vectors = np.vstack([self.vectors, vectors])
            return {"imported": len(validated), "total": len(self.invoices)}


def parse_csv(content):
    if len(content) > 1_000_000:
        raise ValueError("CSV files must be at most 1 MB")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        required = {"id", "client", "description", "amount", "currency", "due_date", "status"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("CSV needs id, client, description, amount, currency, due_date, status columns")
        rows = []
        for row in reader:
            if len(rows) >= 100:
                raise ValueError("Import up to 100 invoices at a time")
            if row.get("tags"):
                row["tags"] = [t.strip() for t in row["tags"].split(";") if t.strip()]
            else:
                row.pop("tags", None)
            rows.append(Invoice.model_validate(row))
        if not rows:
            raise ValueError("The CSV has no invoice rows")
        return rows
    except UnicodeDecodeError as error:
        raise ValueError("Use a UTF-8 CSV file") from error
