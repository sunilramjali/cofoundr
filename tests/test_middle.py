"""Middle (owner: Sunil): extraction checks, pipeline, overdue rules. Fast, no MongoDB/Gemini needed."""
from datetime import datetime, timedelta, timezone

from backend.ai.extract import ExtractedLineItem as L, Extraction, check, to_invoice_doc

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


def test_reminder_gap_prevents_same_day_nagging():
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
