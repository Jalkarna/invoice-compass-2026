from contextlib import asynccontextmanager
from datetime import date
import json
import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .engine import Ledger, LocalEncoder, MODEL, Search, parse_csv

ROOT = Path(__file__).resolve().parent.parent


def create_app(ledger=None, demo=None):
    demo = os.getenv("INVOICE_COMPASS_DEMO", "0") == "1" if demo is None else demo

    @asynccontextmanager
    async def lifespan(app):
        if ledger is not None:
            app.state.ledger = ledger
        else:
            storage = Path(os.getenv("INVOICE_COMPASS_DATA", ROOT / ".data/invoices.json"))
            source = storage if not demo and storage.exists() else ROOT / "sample_invoices.json"
            records = json.loads(source.read_text())
            app.state.ledger = await run_in_threadpool(
                lambda: Ledger(records, LocalEncoder(), None if demo else storage)
            )
        yield

    app = FastAPI(title="Invoice Compass", lifespan=lifespan)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return {"status": "ready", "model": MODEL, "demo": demo}

    @app.get("/api/config")
    def config():
        return {"demo": demo, "model": MODEL, "as_of": date.today().isoformat()}

    @app.get("/api/invoices")
    def invoices(as_of: date | None = None):
        return app.state.ledger.list(as_of or date.today())

    @app.post("/api/search")
    def search(request: Search):
        return app.state.ledger.search(request)

    @app.post("/api/import")
    async def import_invoices(file: UploadFile = File(...)):
        if demo:
            raise HTTPException(403, "The public demo uses fictional data. Run Invoice Compass locally to import your own invoices.")
        content = await file.read(1_000_001)
        try:
            rows = parse_csv(content)
            return await run_in_threadpool(app.state.ledger.add, rows)
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/sample.csv")
    def sample():
        return FileResponse(ROOT / "sample_import.csv", media_type="text/csv", filename="invoice-compass-example.csv")

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static/index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
