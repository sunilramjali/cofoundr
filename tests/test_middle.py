"""Middle (owner: Sunil): extraction checks, pipeline, overdue rules. Fast, no MongoDB/Gemini needed."""
from datetime import datetime, timedelta, timezone  # noqa: F401

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


# ---------------------------------------------------------------- scenarios (in-memory DB, fake Gemini)
import pytest  # noqa: E402
from bson import ObjectId  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

from backend import approvals  # noqa: E402
from backend.ai import extract, overdue  # noqa: E402
from backend.db import client, schema  # noqa: E402
from backend.pipeline import ingest_invoice  # noqa: E402
from tests.conftest import make_invoice  # noqa: E402


def _approval_for(db, invoice_number):
    return db[schema.APPROVALS].find_one({"invoice_number": invoice_number})


# --- extraction checks: edge cases
def test_check_flags_credit_notes_foreign_currency_and_ambiguous_parties():
    assert any("credit note" in i for i in check(make_invoice(line_items=[], printed_total=-20.0)))
    assert any("credit note" in i for i in check(make_invoice(line_items=[L(description="x", qty=1, unit_price=0)], printed_total=0.0)))
    assert any("Currency is EUR" in i for i in check(make_invoice(currency="EUR")))
    assert any("Both issuer and recipient" in i for i in check(make_invoice(issuer_name="GAIA Coffee Supplies Ltd")))
    assert any("Neither" in i for i in check(make_invoice(issuer_name=None, recipient_name=None)))


def test_missing_names_never_crash_doc_building():
    doc = to_invoice_doc(make_invoice(issuer_name=None, recipient_name="GAIA Coffee"), ["x"], 2, "email")
    assert doc["counterparty"] == "Unknown counterparty" and doc["needs_review"]


def test_counterparty_key_normalises_names():
    assert schema.counterparty_key("Eastside Dairy Co.") == schema.counterparty_key("eastside dairy co") == "eastsidedairyco"
    assert schema.counterparty_key(None) == ""


# --- overdue check
def test_overdue_drafts_with_templates_when_gemini_is_down(db, gemini_down):
    new = overdue.run()
    assert [a["ref"] for a in new] == [1, 2, 3, 4]
    assert {a["drafted_by"] for a in new if a["action_type"] == "invoice_reminder"} == {"template"}
    assert [a["action_type"] for a in new].count("supplier_payment") == 1
    log = db[schema.AGENT_LOG].find_one({"kind": "overdue_check"})
    assert log["detail"]["refs"] == [1, 2, 3, 4]


def test_overdue_check_is_idempotent(db, gemini_down):
    overdue.run()
    assert overdue.run() == []
    assert db[schema.APPROVALS].count_documents({"status": "pending"}) == 4


def test_only_one_pending_action_per_invoice_even_under_a_race(db, gemini_down):
    overdue.run()
    a = db[schema.APPROVALS].find_one({"status": "pending"})
    dup = {k: v for k, v in a.items() if k not in ("_id", "ref")}
    with pytest.raises(Exception, match="(?i)duplicate"):
        db[schema.APPROVALS].insert_one(dup)


def test_refs_are_unique_and_keep_counting(db, gemini_down, sent):
    overdue.run()
    approvals.decide(approvals.find(ref=3), "approve", via="dashboard")  # pays Cup & Lid
    inv = dict(db[schema.INVOICES].find_one({"invoice_number": "GAIA-1045"}), _id=ObjectId(), invoice_number="GAIA-2000",
               counterparty_key="x", due_date=seed_today() - timedelta(days=1), status="open")
    db[schema.INVOICES].insert_one(inv)
    assert [a["ref"] for a in overdue.run()] == [5]
    assert client.next_refs(db, "gaia-coffee", 2) == [6, 7]


def test_next_refs_starts_after_existing_refs_on_an_old_database(db):
    db[schema.APPROVALS].insert_one({"business_id": "gaia-coffee", "ref": 10, "status": "approved", "target_id": "t"})
    assert client.next_refs(db, "gaia-coffee", 1) == [11]
    assert client.next_refs(db, "gaia-coffee", 0) == []


def test_stops_chasing_after_the_final_notice(db, gemini_down):
    db[schema.INVOICES].update_one({"invoice_number": "GAIA-1032"}, {"$set": {"reminders_sent": overdue.MAX_REMINDERS}})
    assert "GAIA-1032" not in {a["invoice_number"] for a in overdue.run()}


def test_no_new_reminder_right_after_one_is_sent(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "approve", via="whatsapp")
    inv = db[schema.INVOICES].find_one({"invoice_number": "GAIA-1041"})
    assert len(sent) == 1 and inv["reminders_sent"] == 1 and inv["last_reminder_at"] is not None
    assert "GAIA-1041" not in {a["invoice_number"] for a in overdue.run()}


# --- decisions
def test_reminder_for_an_invoice_paid_meanwhile_is_withdrawn_not_sent(db, gemini_down, sent):
    overdue.run()
    a = _approval_for(db, "GAIA-1038")
    db[schema.INVOICES].update_one({"invoice_number": "GAIA-1038"}, {"$set": {"status": "paid"}})
    with pytest.raises(approvals.DecisionError, match="already paid"):
        approvals.decide(a, "approve", via="dashboard")
    assert sent == [] and db[schema.APPROVALS].find_one({"_id": a["_id"]})["status"] == "cancelled"


def test_overdue_check_withdraws_actions_whose_invoice_was_paid(db, gemini_down):
    overdue.run()
    db[schema.INVOICES].update_one({"invoice_number": "GAIA-1032"}, {"$set": {"status": "paid"}})
    overdue.run()
    assert _approval_for(db, "GAIA-1032")["status"] == "cancelled"
    assert db[schema.AGENT_LOG].find_one({"kind": "approval_cancelled"})


def test_failed_send_goes_back_to_the_queue(db, gemini_down, sent):
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    sent.fail = True
    with pytest.raises(approvals.DecisionError, match="back in the queue"):
        approvals.decide(a, "edit", via="dashboard", edited_draft="New text")
    after = db[schema.APPROVALS].find_one({"_id": a["_id"]})
    assert after["status"] == "pending" and after["draft"] == a["draft"] and "original_draft" not in after
    assert db[schema.INVOICES].find_one({"invoice_number": "GAIA-1041"})["reminders_sent"] == 0
    sent.fail = False
    assert approvals.decide(after, "approve", via="dashboard")["status"] == "approved" and len(sent) == 1


def test_decision_rules(db, gemini_down, sent):
    overdue.run()
    one = approvals.find(ref=1)
    with pytest.raises(approvals.DecisionError, match="needs the new draft"):
        approvals.decide(one, "edit", via="dashboard", edited_draft="   ")
    with pytest.raises(approvals.DecisionError, match="Unknown decision"):
        approvals.decide(one, "maybe", via="dashboard")
    approvals.decide(one, "reject", via="whatsapp")
    with pytest.raises(approvals.DecisionError, match="already rejected"):
        approvals.decide(approvals.find(ref=1), "approve", via="dashboard")
    edited = approvals.decide(approvals.find(ref=2), "edit", via="dashboard", edited_draft="Hi, a quick nudge.")
    stored = approvals.find(ref=2)
    assert edited["status"] == "edited" and stored["original_draft"] != stored["draft"] == "Hi, a quick nudge."
    assert sent[-1]["draft"] == "Hi, a quick nudge."  # the edited text is what goes out


def test_approving_a_supplier_payment_marks_it_paid(db, gemini_down):
    overdue.run()
    result = approvals.decide(_approval_for(db, "CLS-0457"), "approve", via="dashboard")
    assert "marked paid" in result["effect"]
    assert db[schema.INVOICES].find_one({"invoice_number": "CLS-0457"})["status"] == "paid"


def test_find_never_raises_on_bad_input(db):
    assert approvals.find(approval_id="not-an-id") is None
    assert approvals.find(ref="abc") is None and approvals.find(ref=True) is None and approvals.find() is None
    assert approvals.find(ref=999) is None


# --- pipeline (the inputs → middle seam)
def test_pipeline_rejects_unsupported_files_without_saving(db, gemini_down):
    before = db[schema.INVOICES].count_documents({})
    assert ingest_invoice(b"hello", "text/plain", "email").error == "unsupported_type"
    assert ingest_invoice(b"x", "", "email").error == "unsupported_type"
    # MIME parameters and case are fine: it gets past the type check (then Gemini is down)
    assert ingest_invoice(b"%PDF", "Application/PDF; charset=binary", "email").error == "gemini_unavailable"
    assert db[schema.INVOICES].count_documents({}) == before


def test_pipeline_reports_gemini_quota_as_try_again(db, gemini_down):
    r = ingest_invoice(b"img", "image/png", "email", origin="email from x")
    assert not r.ok and r.error == "gemini_unavailable" and "Try again" in r.message
    assert db[schema.AGENT_LOG].find_one({"kind": "ingest_failed"})["detail"]["error"] == "gemini_unavailable"


@pytest.mark.parametrize("exc", [genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}}),
                                 ValueError("Gemini returned an empty response."), ConnectionError("network")])
def test_pipeline_never_crashes_on_bad_model_output_or_network(db, monkeypatch, exc):
    def boom(*_a, **_k):
        raise exc
    monkeypatch.setattr(extract, "_ask_gemini", boom)
    r = ingest_invoice(b"img", "image/png", "manual")
    assert not r.ok and r.error == "unreadable"


def test_pipeline_does_not_save_things_that_are_not_invoices(db, fake_extraction):
    fake_extraction(make_invoice(issuer_name=None, recipient_name=None, invoice_number=None, line_items=[], printed_total=None))
    before = db[schema.INVOICES].count_documents({})
    r = ingest_invoice(b"cat", "image/jpeg", "email")
    assert r.error == "not_invoice" and db[schema.INVOICES].count_documents({}) == before


def test_pipeline_happy_path_overdue_customer_invoice_gets_drafted(db, fake_extraction):
    fake_extraction(make_invoice(issuer_name="GAIA Coffee", recipient_name="Test Cafe Ltd", recipient_email="t@example.com",
                                 invoice_number="GAIA-3000", due_date="2026-10-01"))
    r = ingest_invoice(b"img", "image/png", "email", origin="email from t@example.com")
    assert r.ok and r.created and not r.needs_review and "checked ✓ (attempt 1)" in r.message
    assert r.invoice["direction"] == "receivable" and r.invoice["status"] == "overdue"
    assert "GAIA-3000" in {a["invoice_number"] for a in r.drafted}
    log = db[schema.AGENT_LOG].find_one({"kind": "invoice_extracted"})
    assert log["detail"]["invoice_id"] == str(r.invoice["_id"])


def test_pipeline_inconsistent_invoice_is_flagged_and_not_chased(db, fake_extraction):
    bad = make_invoice(issuer_name="GAIA Coffee", recipient_name="Test Cafe", invoice_number="GAIA-3001",
                       due_date="2026-10-01", printed_total=99.0)
    fake_extraction(bad, bad)
    r = ingest_invoice(b"img", "image/png", "email")
    assert r.created and r.needs_review and "needs your review" in r.message and ".." not in r.message
    assert r.invoice["extraction_confidence"] < 0.5
    assert "GAIA-3001" not in {a["invoice_number"] for a in r.drafted}


def test_pipeline_second_look_fixes_a_misread(db, fake_extraction):
    fake_extraction(make_invoice(printed_total=99.0), make_invoice())
    r = ingest_invoice(b"img", "image/png", "email")
    assert r.created and not r.needs_review and "attempt 2" in r.message and r.invoice["extraction_confidence"] == 0.9


def test_duplicates_are_per_counterparty(db, fake_extraction):
    fake_extraction(make_invoice(invoice_number="INV-001"))
    assert ingest_invoice(b"a", "image/png", "email").created
    assert not ingest_invoice(b"a", "image/png", "email").created  # same invoice again
    fake_extraction(make_invoice(invoice_number="INV-001", issuer_name="Eastside Dairy Co."))
    assert not ingest_invoice(b"a", "image/png", "email").created  # same supplier, punctuation differs
    fake_extraction(make_invoice(invoice_number="INV-001", issuer_name="Bow Lane Roasters"))
    assert ingest_invoice(b"a", "image/png", "email").created  # different supplier, same number: a new invoice


def test_ensure_indexes_migrates_an_older_database(monkeypatch):
    import mongomock
    mock = mongomock.MongoClient(tz_aware=True)
    monkeypatch.setattr(client, "get_client", lambda: mock)
    old = client.get_db()
    old[schema.INVOICES].create_index([("business_id", 1), ("invoice_number", 1)], unique=True)
    old[schema.INVOICES].insert_one({"business_id": "gaia-coffee", "invoice_number": "A", "direction": "payable", "counterparty": "Eastside Dairy Co."})
    client.ensure_indexes(old)
    client.ensure_indexes(old)  # twice: idempotent
    assert "business_id_1_invoice_number_1" not in old[schema.INVOICES].index_information()
    assert old[schema.INVOICES].find_one({"invoice_number": "A"})["counterparty_key"] == "eastsidedairyco"


def seed_today():
    from data.seed import _today
    return _today()
