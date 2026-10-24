"""Fast checks that need no MongoDB or Gemini (run in CI on every PR)."""
from fastapi.testclient import TestClient

from backend.ai.extract import ExtractedLineItem as L, Extraction, check, to_invoice_doc
from bridge.main import app

BASE = dict(issuer_name="Bow Lane Roasters", recipient_name="GAIA Coffee", invoice_number="BLR-1",
            issue_date="2026-10-20", due_date="2026-10-30",
            line_items=[L(description="Espresso 1kg", qty=10, unit_price=19.5), L(description="Decaf 1kg", qty=2, unit_price=21.0)])


def test_consistent_invoice_passes():
    assert check(Extraction(**BASE, printed_total=237.0)) == []


def test_total_mismatch_is_flagged_not_fixed():
    ex = Extraction(**BASE, printed_total=263.0)
    issues = check(ex)
    assert issues and "237.00" in issues[0]
    doc = to_invoice_doc(ex, issues, attempts=2, source="pdf")
    assert doc["needs_review"] is True and doc["amount"] == 263.0 and doc["extraction_confidence"] < 0.5


def test_bad_dates_are_flagged():
    assert any("Due date" in i for i in check(Extraction(**{**BASE, "due_date": None}, printed_total=237.0)))
    assert any("before" in i for i in check(Extraction(**{**BASE, "due_date": "2026-10-01"}, printed_total=237.0)))


def test_direction_from_issuer():
    payable = to_invoice_doc(Extraction(**BASE, printed_total=237.0), [], 1, "pdf")
    assert payable["direction"] == "payable" and payable["counterparty"] == "Bow Lane Roasters"
    receivable = to_invoice_doc(Extraction(**{**BASE, "issuer_name": "GAIA Coffee", "recipient_name": "Hackney Hub"},
                                           printed_total=237.0), [], 1, "pdf")
    assert receivable["direction"] == "receivable" and receivable["counterparty"] == "Hackney Hub"


def test_health_and_token_guard():
    client = TestClient(app)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/brief").status_code == 403
    assert client.post("/approve", json={"text": "approve 1"}).status_code == 403
