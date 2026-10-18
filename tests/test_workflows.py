import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invoice_compass.engine import Ledger, LocalEncoder, Search
from invoice_compass.web import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def encoder():
    return LocalEncoder()


@pytest.fixture
def ledger(encoder, tmp_path):
    return Ledger(json.loads((ROOT / "sample_invoices.json").read_text()), encoder, tmp_path / "records.json")


def test_real_model_finds_product_shoot_without_matching_client_name(ledger):
    result = ledger.search(Search(query="product photos for a catalog", status="unpaid"))
    assert result["results"][0]["invoice"]["id"] == "INV-2610"
    assert result["results"][0]["similarity"] > 0.3


def test_exact_totals_keep_currencies_separate(ledger):
    result = ledger.list(date(2026, 10, 18))
    assert result["totals"] == {
        "INR": {"unpaid": "93000.00", "overdue": "88500.00"},
        "USD": {"unpaid": "500.00", "overdue": "320.00"},
    }


def test_overdue_filter_does_not_include_paid_or_future_invoices(ledger):
    result = ledger.search(Search(query="client work", overdue_only=True, as_of=date(2026, 10, 18), min_score=0, limit=20))
    assert result["results"]
    for row in result["results"]:
        assert row["invoice"]["status"] == "unpaid"
        assert row["invoice"]["due_date"] < "2026-10-18"
    assert not {"INV-2603", "INV-2604", "INV-2611", "INV-2612"}.intersection(r["invoice"]["id"] for r in result["results"])


def test_csv_import_persists_and_rejects_duplicate_batch_atomically(ledger):
    with TestClient(create_app(ledger, demo=False)) as client:
        content = (ROOT / "sample_import.csv").read_bytes()
        response = client.post("/api/import", files={"file": ("invoices.csv", content, "text/csv")})
        assert response.status_code == 200
        assert response.json() == {"imported": 1, "total": 13}
        saved = json.loads(ledger.storage.read_text())
        assert saved[-1]["id"] == "MY-001"
        second = client.post("/api/import", files={"file": ("invoices.csv", content, "text/csv")})
        assert second.status_code == 422
        assert json.loads(ledger.storage.read_text()) == saved
        result = client.post("/api/search", json={"query": "website redesign and booking calendar", "status": "unpaid"})
        assert result.json()["results"][0]["invoice"]["id"] == "MY-001"


@pytest.mark.parametrize("bad_amount", ["NaN", "Infinity", "-1", "12.345"])
def test_invalid_amount_never_imports_partial_file(ledger, bad_amount):
    content = (ROOT / "sample_import.csv").read_text().replace("12000.00", bad_amount)
    with TestClient(create_app(ledger, demo=False)) as client:
        response = client.post("/api/import", files={"file": ("bad.csv", content.encode(), "text/csv")})
        assert response.status_code == 422
        assert len(client.get("/api/invoices").json()["invoices"]) == 12
        assert not ledger.storage.exists()


def test_demo_is_read_only_and_empty_queries_are_rejected(ledger):
    with TestClient(create_app(ledger, demo=True)) as client:
        assert client.get("/health").json()["demo"] is True
        response = client.post("/api/import", files={"file": ("x.csv", (ROOT / "sample_import.csv").read_bytes())})
        assert response.status_code == 403
        assert client.post("/api/search", json={"query": "   "}).status_code == 422
        assert "frame-ancestors 'none'" in client.get("/").headers["Content-Security-Policy"]


def test_oversized_and_invalid_encoding_files_are_rejected(ledger):
    with TestClient(create_app(ledger, demo=False)) as client:
        for payload in [b"a" * 1_000_001, b"\xff\xfe\xfa"]:
            assert client.post("/api/import", files={"file": ("bad.csv", payload)}).status_code == 422
        assert len(ledger.invoices) == 12
