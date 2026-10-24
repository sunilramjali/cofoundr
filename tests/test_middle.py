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
    assert ingest_invoice(b"PK", "application/zip", "email").error == "unsupported_type"
    assert ingest_invoice(b"x", "", "email").error == "unsupported_type"
    # text is allowed: invoices written in an email body (Gmail intake)
    assert ingest_invoice(b"Invoice 7802", "text/plain", "email").error == "gemini_unavailable"
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


# --- till data: backend.sales (Jade's POS connector → MongoDB)
import copy  # noqa: E402

from backend import sales  # noqa: E402
from backend.inputs.pos.client import PosClient  # noqa: E402

CLOCK = "2026-12-01"  # after the last of Jade's synthetic till data (Sep–Nov 2026)
POS = PosClient(now=CLOCK)


def _orders(since="2026-10-02", until="2026-10-03"):
    return list(POS.iter_sales(since=since, until=until))


def test_all_of_jades_pos_data_records_cleanly(db):
    every = list(POS.iter_sales())
    r = sales.record_orders(now=CLOCK, orders=every)
    assert r.errors == [] and r.skipped == 0 and r.new == len(every) == 3969
    assert db[schema.ORDERS].count_documents({}) == 3969
    assert db[schema.SALES].count_documents({}) == sum(len(o["items"]) for o in every)
    assert round(r.revenue, 2) == round(sum(o["total_gbp"] for o in every), 2)
    menu = {p["_id"]: p for p in db[schema.PRODUCTS].find()}
    assert len(menu) == 15 and menu["FLW"]["name"] == "Flat White" and menu["FLW"]["category"] == "coffee"
    assert set(menu["PAT"]["variants"]) == {"Almond Croissant", "Butter Croissant", "Pain au Chocolat"}


def test_sync_is_idempotent_and_overlapping_windows_are_fine(db):
    first = sales.record_orders(now=CLOCK, orders=_orders("2026-10-02", "2026-10-04"))
    again = sales.record_orders(now=CLOCK, orders=_orders("2026-10-03", "2026-10-06"))  # overlaps one day
    assert first.new > 0 and again.duplicates == len(_orders("2026-10-03", "2026-10-04")) and again.errors == []
    assert db[schema.ORDERS].count_documents({}) == len(_orders("2026-10-02", "2026-10-06"))


def test_timestamps_are_stored_in_utc(db):
    o = _orders()[0]  # "2026-10-02T07:16:06+01:00" (London summer time)
    sales.record_orders(now=CLOCK, orders=[o])
    stored = db[schema.ORDERS].find_one({"order_id": o["order_id"]})
    assert stored["placed_at"].hour == 6 and stored["placed_at"].utcoffset().total_seconds() == 0
    naive = dict(copy.deepcopy(o), order_id="NAIVE-1", placed_at="2026-10-02T07:16:06")  # no offset → London
    sales.record_orders(now=CLOCK, orders=[naive])
    assert db[schema.ORDERS].find_one({"order_id": "NAIVE-1"})["placed_at"].hour == 6


def test_cancelled_and_refunded_orders_are_skipped(db):
    a, b = copy.deepcopy(_orders()[:2])
    a["order_status"] = "cancelled"
    b["payment"]["status"] = "refunded"
    r = sales.record_orders(now=CLOCK, orders=[a, b])
    assert r.skipped == 2 and r.new == 0 and db[schema.ORDERS].count_documents({}) == 0


@pytest.mark.parametrize("breakage,reason", [
    (lambda o: o.update(order_id=""), "missing order_id"),
    (lambda o: o.update(items=[]), "no items"),
    (lambda o: o.update(placed_at="2026-12-02T09:00:00+00:00"), "future"),  # after the pinned clock
    (lambda o: o.update(placed_at="yesterday"), "Invalid isoformat"),
    (lambda o: o["items"][0].update(quantity=-1), "refunds"),
    (lambda o: o["items"][0].update(quantity="2"), "must be a number"),
    (lambda o: o["items"][0].update(sku=""), "missing sku"),
    (lambda o: o["items"][0].update(unit_price_gbp=-2.0), "negative price"),
    (lambda o: o["items"][0].update(line_total_gbp=99.0), "line total"),
    (lambda o: o.update(total_gbp=o["total_gbp"] + 5), "totals don't add up"),
])
def test_bad_orders_are_rejected_with_a_reason_and_the_rest_still_recorded(db, breakage, reason):
    bad, good = copy.deepcopy(_orders()[:2])
    breakage(bad)
    r = sales.record_orders(now=CLOCK, orders=[bad, good, "not even a dict"])
    assert r.new == 1 and len(r.errors) == 2 and reason.lower() in r.errors[0].lower()
    assert db[schema.ORDERS].count_documents({}) == 1


def test_a_failed_line_save_releases_the_order_so_the_next_sync_retries(db, monkeypatch):
    o = _orders()[0]
    real = db[schema.SALES].insert_many
    monkeypatch.setattr(db[schema.SALES].__class__, "insert_many", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    r = sales.record_orders(now=CLOCK, orders=[o])
    assert r.new == 0 and "will retry" in r.errors[0] and db[schema.ORDERS].count_documents({}) == 0
    monkeypatch.setattr(db[schema.SALES].__class__, "insert_many", real)
    assert sales.record_orders(now=CLOCK, orders=[o]).new == 1


def test_one_log_line_per_sync_not_per_order(db):
    sales.record_orders(now=CLOCK, orders=_orders("2026-10-02", "2026-10-06"))
    logs = list(db[schema.AGENT_LOG].find({"kind": "sales_synced"}))
    assert len(logs) == 1 and "new order(s)" in logs[0]["summary"]
    sales.record_orders(now=CLOCK, orders=_orders("2026-10-02", "2026-10-06"))  # all duplicates: nothing worth logging
    assert db[schema.AGENT_LOG].count_documents({"kind": "sales_synced"}) == 1


def test_stock_counts_are_stored_once_with_their_kind(db):
    counts = POS.list_stock_counts()
    assert len(counts) == 6 and all(sales.record_stock_count(c) for c in counts)
    assert not any(sales.record_stock_count(c) for c in counts)  # idempotent
    latest = sales.latest_stock()
    assert latest["kind"] == "closing" and latest["items"][0]["sku"].startswith("INV-")
    assert {c["kind"] for c in db[schema.STOCK_COUNTS].find()} == {"opening", "closing"}
    with pytest.raises(ValueError):
        sales.record_stock_count({"record_type": "something_else"})


def test_till_sync_never_reaches_the_future_through_the_connector(db):
    r = sales.record_orders(PosClient(now="2026-10-03").iter_sales(), now="2026-10-03")
    assert r.new > 0 and db[schema.ORDERS].find_one(sort=[("placed_at", -1)])["placed_at"].date().isoformat() <= "2026-10-02"


# --- forecast (baseline from till history)
from datetime import date as _date  # noqa: E402
from statistics import mean as _mean  # noqa: E402
from zoneinfo import ZoneInfo as _Zone  # noqa: E402

from backend.ai import forecast  # noqa: E402

_LON = _Zone("Europe/London")


def _hist(weekday_revenue, weeks=6, end=_date(2026, 10, 24)):
    """Synthetic daily history: {weekday: revenue} for `weeks` weeks before `end`."""
    h = {}
    for w in range(1, weeks + 1):
        for wd, rev in weekday_revenue.items():
            d = end - timedelta(days=(end.weekday() - wd) % 7 or 7) - timedelta(weeks=w - 1)
            h[d] = {"revenue": rev + w, "orders": 10 + w}
    return h


def test_forecast_learns_trading_days_and_averages_same_weekdays():
    h = _hist({0: 600, 2: 500, 4: 550, 5: 700})  # Mon, Wed, Fri, Sat
    out = forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=7)  # starts on a Saturday
    assert [f["trading"] for f in out] == [True, False, True, False, True, False, True]  # Sat Sun Mon Tue Wed Thu Fri
    sat = out[0]
    assert sat["expected_revenue"] == round(_mean(700 + w for w in range(1, 7)), 2)
    assert sat["low_revenue"] == 701 and sat["high_revenue"] == 706 and "last 6 Saturdays" in sat["basis"]
    assert out[1]["expected_revenue"] == 0 and "doesn't usually trade on Sundays" in out[1]["basis"]
    assert all(f["final_revenue"] == f["expected_revenue"] and f["adjustment"] is None for f in out)


def test_forecast_with_little_or_no_history():
    assert all(not f["trading"] for f in forecast.baseline({}, {}, {}, _date(2026, 10, 24)))
    short = _hist({5: 700}, weeks=3)  # 3 Saturdays: enough to count as a trading day, basis says so
    sat = forecast.baseline(short, {}, {}, _date(2026, 10, 24), days=1)[0]
    assert sat["trading"] and "last 3 Saturdays" in sat["basis"]


def test_a_one_off_day_does_not_make_a_weekday_a_trading_day():
    h = _hist({5: 700})
    h[_date(2026, 10, 13)] = {"revenue": 900, "orders": 90}  # one Tuesday market, once
    tue = forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=4)[3]
    assert tue["weekday"] == "Tuesday" and not tue["trading"]


def test_product_forecast_counts_days_without_a_sale_as_zero():
    h = _hist({5: 700}, weeks=2)
    sats = sorted(h)
    pq = {sats[0]: {"FLW": 30, "IMA": 4}, sats[1]: {"FLW": 20}}  # iced matcha only sold on one of two Saturdays
    sat = forecast.baseline(h, pq, {"FLW": "Flat White"}, _date(2026, 10, 24), days=1)[0]
    by = {p["sku"]: p for p in sat["products"]}
    assert by["FLW"]["expected_qty"] == 25 and by["FLW"]["name"] == "Flat White" and by["IMA"]["expected_qty"] == 2


def test_days_are_bucketed_in_london_time_across_the_clock_change():
    from datetime import datetime as _dt, timezone as _tz
    # 23:30 UTC on Sat 24 Oct is 00:30 BST on Sun 25; 23:30 UTC on Sun 25 (after clocks go back) is still Sun 25
    orders = [{"placed_at": _dt(2026, 10, 24, 23, 30, tzinfo=_tz.utc), "total": 5.0},
              {"placed_at": _dt(2026, 10, 25, 23, 30, tzinfo=_tz.utc), "total": 7.0}]
    assert forecast.daily_history(orders) == {_date(2026, 10, 25): {"revenue": 12.0, "orders": 2}}


def test_forecast_run_on_jades_data_matches_a_manual_calculation(db):
    sales.record_orders(POS.iter_sales(until="2026-10-24"), now=CLOCK)
    out = forecast.run(now=datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc))
    sat = out[0]
    # by hand from the raw export: total per Saturday, last 6 Saturdays before 24 Oct
    per_day = {}
    for o in POS.iter_sales(until="2026-10-24"):
        d = datetime.fromisoformat(o["placed_at"]).astimezone(_LON).date()
        per_day[d] = per_day.get(d, 0) + o["total_gbp"]
    last6 = sorted((d for d in per_day if d.weekday() == 5), reverse=True)[:6]
    assert sat["weekday"] == "Saturday" and sat["expected_revenue"] == round(_mean(per_day[d] for d in last6), 2)
    assert [f["weekday"] for f in out if f["trading"]] == ["Saturday", "Monday", "Wednesday", "Friday"]
    assert sat["products"][0]["name"] in {"Flat White", "Latte", "Cappuccino"}


def test_forecast_run_stores_latest_and_ignores_today_so_far(db):
    sales.record_orders(POS.iter_sales(until="2026-10-24T12:00:00+01:00"), now=CLOCK)  # includes Sat morning sales
    now = datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc)
    first = forecast.run(now=now)
    db[schema.ORDERS].insert_one({"_id": "x", "business_id": "gaia-coffee", "placed_at": now - timedelta(hours=1), "total": 9999.0})
    second = forecast.run(now=now)  # a huge sale this morning must not move today's forecast (today isn't over)
    assert first[0]["expected_revenue"] == second[0]["expected_revenue"]
    assert db[schema.FORECASTS].count_documents({}) == 7  # replaced, not duplicated
    assert db[schema.AGENT_LOG].count_documents({"kind": "forecast_updated"}) == 2
    assert forecast.run(now=now, dry_run=True) and db[schema.AGENT_LOG].count_documents({"kind": "forecast_updated"}) == 2


def test_a_new_business_with_two_weeks_of_history_still_gets_a_forecast():
    h = _hist({0: 600, 5: 700}, weeks=2)
    out = forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=3)  # Sat, Sun, Mon
    assert [f["trading"] for f in out] == [True, False, True] and "last 2 Saturdays" in out[0]["basis"]


# --- Gemini retry with backoff
from types import SimpleNamespace  # noqa: E402

from backend.ai.gemini import with_retry  # noqa: E402


def _api_error(code):
    return (genai_errors.ClientError if code < 500 else genai_errors.ServerError)(code, {"error": {"code": code, "message": "x"}})


def _flaky(*outcomes):
    """A call that raises/returns each outcome in turn; counts how often it was made."""
    left = list(outcomes)

    def call():
        call.n += 1
        out = left.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
    call.n = 0
    return call


def test_gemini_retries_busy_errors_then_succeeds():
    call = _flaky(_api_error(429), _api_error(503), "ok")
    assert with_retry(call) == "ok" and call.n == 3


def test_gemini_gives_up_after_three_attempts_and_raises():
    call = _flaky(*[_api_error(429)] * 5)
    with pytest.raises(Exception) as e:
        with_retry(call)
    assert e.value.code == 429 and call.n == 3


def test_gemini_does_not_retry_a_bad_request():
    call = _flaky(_api_error(400), "ok")
    with pytest.raises(Exception):
        with_retry(call)
    assert call.n == 1


def test_extraction_survives_one_busy_answer_from_gemini(monkeypatch):
    good = Extraction(**BASE, printed_total=237.0).model_dump_json()
    call = _flaky(_api_error(429), SimpleNamespace(text=good))
    fake = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_k: call()))
    monkeypatch.setattr(extract, "get_client", lambda: fake)
    ex, issues, attempts = extract.extract_invoice(b"%PDF", "application/pdf")
    assert issues == [] and attempts == 1 and call.n == 2


def test_template_fallback_is_logged_for_the_owner(db, gemini_down):
    new = overdue.run()
    log = db[schema.AGENT_LOG].find_one({"kind": "gemini_fallback"})
    assert log and log["detail"]["refs"] == [a["ref"] for a in new if a["drafted_by"] == "template"]
    overdue.run()  # nothing new drafted → no second fallback line
    assert db[schema.AGENT_LOG].count_documents({"kind": "gemini_fallback"}) == 1
