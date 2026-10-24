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


def test_update_messages_are_whatsapp_ready():
    from datetime import datetime, timezone
    from backend.outputs.updates import approval_message, invoice_message
    ok = invoice_message({"invoice_number": "EDC-1", "counterparty": "Eastside Dairy Co", "amount": 176.7,
                          "due_date": datetime(2026, 11, 7, tzinfo=timezone.utc), "needs_review": False})
    assert "EDC-1" in ok and "£176.70" in ok and "07 Nov" in ok and "✓" in ok
    bad = invoice_message({"invoice_number": "BLR-1", "counterparty": "Bow Lane", "amount": 263.0, "needs_review": True,
                           "verification_issues": ["Line items add up to 237.00 but the printed total is 263.00."]})
    assert bad.startswith("⚠️") and "237.00" in bad
    msg = approval_message({"ref": 4, "title": "Chase Hackney Hub Coworking: £450.00, 3 days overdue"})
    assert 'approve 4' in msg and 'reject 4' in msg


def test_updates_requires_token():
    assert TestClient(app).get("/updates").status_code == 403


def test_reminder_gap_prevents_same_day_nagging():
    from datetime import datetime, timedelta, timezone
    from backend.ai.overdue import due_for_action
    today = datetime(2026, 10, 24, tzinfo=timezone.utc)
    assert due_for_action({"direction": "receivable"}, today)  # never reminded
    assert not due_for_action({"direction": "receivable", "last_reminder_at": today - timedelta(hours=2)}, today)
    assert due_for_action({"direction": "receivable", "last_reminder_at": today - timedelta(days=4)}, today)
    assert due_for_action({"direction": "payable", "last_reminder_at": today}, today)


def test_pipeline_rejects_empty_and_oversized_input_without_calling_gemini():
    from backend.pipeline import MAX_BYTES, ingest_invoice
    assert ingest_invoice(b"", "image/png", "email").error == "empty"
    assert ingest_invoice(b"x" * (MAX_BYTES + 1), "image/png", "email").error == "too_large"
