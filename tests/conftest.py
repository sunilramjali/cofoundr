"""Shared test fixtures (owners: both). No network: in-memory MongoDB (mongomock) + a fake Gemini."""
import os
from datetime import datetime, timezone

import mongomock
import pytest
from google.genai import errors as genai_errors

os.environ.setdefault("BRIEF_TOKEN", "test-token")
os.environ["MONGODB_DB"] = "cofoundr-test"

TOKEN = os.environ["BRIEF_TOKEN"]


@pytest.fixture(autouse=True)
def no_real_services(monkeypatch):
    """Tests never reach the real Atlas cluster or Gemini, even when .env has real keys."""
    from backend.ai import extract, forecast, gemini, overdue, prioritise
    from backend.db import client

    def no_db():
        raise RuntimeError("Use the `db` fixture: tests must not touch the real MongoDB.")

    def no_gemini(*_a, **_k):
        raise RuntimeError("Use gemini_down / fake_extraction: tests must not call the real Gemini.")

    monkeypatch.setattr(client, "get_client", no_db)
    monkeypatch.setattr(extract, "get_client", no_gemini)
    monkeypatch.setattr(overdue, "get_client", no_gemini)
    monkeypatch.setattr(prioritise, "get_client", no_gemini)
    monkeypatch.setattr(forecast, "get_client", no_gemini)
    monkeypatch.setattr(gemini, "_sleep", lambda _s: None)  # retries back off instantly in tests


@pytest.fixture
def db(monkeypatch):
    """A fresh in-memory database seeded with the synthetic GAIA Coffee data and real indexes."""
    from backend.db import client, schema
    from data import seed

    mock = mongomock.MongoClient(tz_aware=True)
    monkeypatch.setattr(client, "get_client", lambda: mock)
    database = client.get_db()
    now = datetime.now(timezone.utc)
    client.ensure_indexes(database)
    database[schema.BUSINESSES].insert_one(seed.build_business(now))
    database[schema.INVOICES].insert_many(seed.build_invoices(seed._today(), now))
    return database


@pytest.fixture
def gemini_down(monkeypatch):
    """Every Gemini call fails with the 429 spend-cap error we saw on Saturday."""
    from backend.ai import extract, forecast, overdue, prioritise

    def boom(*_a, **_k):
        raise genai_errors.ClientError(429, {"error": {"code": 429, "message": "spending cap", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(extract, "_ask_gemini", boom)
    monkeypatch.setattr(overdue, "get_client", boom)
    monkeypatch.setattr(prioritise, "get_client", boom)
    monkeypatch.setattr(forecast, "get_client", boom)


@pytest.fixture
def fake_extraction(monkeypatch, gemini_down):
    """Make Gemini 'read' a given Extraction (or a sequence, one per call). Drafting stays on templates."""
    from backend.ai import extract

    def use(*extractions):
        calls = list(extractions)
        monkeypatch.setattr(extract, "_ask_gemini", lambda *_a, **_k: calls.pop(0) if len(calls) > 1 else calls[0])
    return use


@pytest.fixture
def sent(monkeypatch):
    """Record reminder emails instead of sending; set sent.fail = True to simulate a failed send."""
    from backend.outputs import email

    class Outbox(list):
        fail = False

    box = Outbox()

    def send(approval):
        if box.fail:
            raise ConnectionError("SMTP down")
        box.append(approval)
        return f"reminder sent to {approval.get('to_email')} (test)"
    monkeypatch.setattr(email, "send_reminder", send)
    return box


def make_invoice(**over):
    """An Extraction for a GAIA-billed supplier invoice; override any field."""
    from backend.ai.extract import ExtractedLineItem as L, Extraction
    base = dict(issuer_name="Eastside Dairy Co", issuer_email="billing@eastsidedairy.example.com", recipient_name="GAIA Coffee",
                invoice_number="EDC-9001", issue_date="2026-10-01", due_date="2030-01-31", currency="GBP",
                line_items=[L(description="Oat milk 1L", qty=10, unit_price=2.0)], printed_total=20.0)
    return Extraction(**{**base, **over})
