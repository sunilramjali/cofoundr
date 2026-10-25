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

CLOCK = "2026-12-04"  # after the last of Jade's synthetic till data (4 Sep – 3 Dec 2026, Thu–Sun)
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
    (lambda o: o.update(placed_at="2026-12-05T09:00:00+00:00"), "future"),  # after the pinned clock
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
    assert [f["weekday"] for f in out if f["trading"]] == ["Saturday", "Sunday", "Thursday", "Friday"]
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


def test_a_closed_or_broken_day_is_left_out_of_the_average_and_named():
    h = _hist({5: 500}, weeks=5)
    sats = sorted(h)
    h[sats[2]] = {"revenue": 7.0, "orders": 2}  # card machine down / closed early
    sat = forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=1)[0]
    normal = [h[d]["revenue"] for d in sats if d != sats[2]]
    assert sat["expected_revenue"] == round(_mean(normal), 2) and sat["low_revenue"] == min(normal)
    assert sat["left_out"] == [sats[2].isoformat()] and "leaving out" in sat["basis"] and "far below a usual Saturday" in sat["basis"]


def test_too_few_days_to_judge_means_nothing_is_left_out():
    h = _hist({5: 500}, weeks=2)
    h[sorted(h)[0]] = {"revenue": 7.0, "orders": 2}
    sat = forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=1)[0]
    assert sat["left_out"] == [] and "leaving out" not in sat["basis"]


def _sats(revenues):
    """{Saturday: day} for the Saturdays before 24 Oct, newest first."""
    return {_date(2026, 10, 24) - timedelta(weeks=i + 1): {"revenue": r, "orders": 10} for i, r in enumerate(revenues)}


@pytest.mark.parametrize("revenues, expected, left_out", [
    ([7, 500, 500, 500], 500, ["2026-10-17"]),  # the most recent day was the odd one
    ([500, 7, 500, 500, 500, 500, 500], 500, ["2026-10-10"]),  # a 7th week fills the gap: still 6 days averaged
    ([500, -10, 500], 500, ["2026-10-10"]),  # refunds outweighed sales
    ([500, 7, 5], 170.67, []),  # most days tiny: can't tell what's usual, so nothing is guessed away
    ([500, 450, 400, 150, 100], 320, []),  # a business ramping up: early quiet days are real
])
def test_which_unusual_days_are_left_out(revenues, expected, left_out):
    sat = forecast.baseline(_sats(revenues), {}, {}, _date(2026, 10, 24), days=1)[0]
    assert sat["expected_revenue"] == expected and sat["left_out"] == left_out


def test_a_quiet_but_real_day_is_kept():
    h = _hist({5: 500}, weeks=4)
    h[sorted(h)[1]] = {"revenue": 200.0, "orders": 30}  # a wet Saturday: 40% of usual, still a trading day
    assert forecast.baseline(h, {}, {}, _date(2026, 10, 24), days=1)[0]["left_out"] == []


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


# --- prioritisation
from backend.ai import prioritise  # noqa: E402


def _ranked_by_gemini(monkeypatch, pick):
    """Fake Gemini: `pick(ids)` returns the ids in the order Gemini 'chose' (may drop or invent some)."""
    def fake(tasks):
        return {i: prioritise.Ranked(id=i, impact="high", reason=f"because {i[-4:]}") for i in pick([t["_id"] for t in tasks])}
    monkeypatch.setattr(prioritise, "gemini_rank", fake)


def _setup_tasks(db, fake_extraction):
    overdue.run()  # 4 pending approvals (templates: Gemini is down)
    fake_extraction(make_invoice(invoice_number="EDC-BAD", printed_total=99.0))  # total doesn't match → review
    assert ingest_invoice(b"%PDF", "application/pdf", "pdf").needs_review
    sales.record_orders(POS.iter_sales(until="2026-10-24"), now=CLOCK)
    forecast.run(now=datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc))


def test_tasks_are_gathered_from_the_database(db, fake_extraction):
    _setup_tasks(db, fake_extraction)
    tasks = prioritise.candidates(db, datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc))
    kinds = [t["kind"] for t in tasks]
    assert kinds.count("approval") == 4 and kinds.count("review_invoice") == 1 and kinds.count("prep_day") == 1
    prep = next(t for t in tasks if t["kind"] == "prep_day")
    assert "today" in prep["title"] and "Flat White" in prep["facts"] or "Latte" in prep["facts"]
    assert {t["approval_ref"] for t in tasks if t["kind"] == "approval"} == {1, 2, 3, 4}


def test_gemini_order_is_kept_and_its_mistakes_corrected(db, fake_extraction, monkeypatch):
    _setup_tasks(db, fake_extraction)
    # Gemini reverses the list, invents a task and forgets one
    _ranked_by_gemini(monkeypatch, lambda ids: [f"{prioritise.BUSINESS_ID}:approval:made-up"] + ids[::-1][:-1])
    out = prioritise.run(now=datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc))
    assert [t["rank"] for t in out] == list(range(1, 7))
    assert "made-up" not in str([t["_id"] for t in out])
    assert [t["ranked_by"] for t in out] == ["gemini"] * 5 + ["rule"]
    assert db[schema.TASKS].count_documents({}) == 6
    log = db[schema.AGENT_LOG].find_one({"kind": "gemini_fallback", "summary": {"$regex": "Priorities"}})
    assert len(log["detail"]["invented"]) == 1 and len(log["detail"]["missed"]) == 1


def test_priorities_fall_back_to_rules_when_gemini_is_down(db, fake_extraction):
    _setup_tasks(db, fake_extraction)  # gemini_down is active through fake_extraction
    out = prioritise.run(now=datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc))
    assert out and {t["ranked_by"] for t in out} == {"rule"}
    assert [t["rank"] for t in out] == list(range(1, len(out) + 1))
    assert all(t["reason"].startswith("Rule:") for t in out)
    assert "unavailable (429)" in db[schema.AGENT_LOG].find_one({"kind": "gemini_fallback", "summary": {"$regex": "Priorities"}})["summary"]


def test_rule_ranking_puts_the_most_money_and_urgency_first():
    def appr(amount, days):
        return {"_id": f"a{amount}", "kind": "approval", "facts": "x", "score": amount * (1 + days / 7)}
    out = prioritise.rule_rank([appr(50, 2), appr(400, 20), appr(400, 1)])
    assert [t["_id"] for t in out] == ["a400", "a400", "a50"] and out[0]["score"] > out[1]["score"]


def test_reranking_replaces_the_list_and_drops_done_tasks(db, fake_extraction):
    _setup_tasks(db, fake_extraction)
    now = datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc)
    first = prioritise.run(now=now)
    db[schema.APPROVALS].update_many({"ref": {"$in": [1, 2]}}, {"$set": {"status": "approved"}})
    second = prioritise.run(now=now)
    assert len(second) == len(first) - 2 and db[schema.TASKS].count_documents({}) == len(second)
    assert prioritise.run(now=now, dry_run=True) and db[schema.AGENT_LOG].count_documents({"kind": "tasks_ranked"}) == 2


def test_nothing_to_do_means_an_empty_list_and_no_gemini_call(db, monkeypatch):
    monkeypatch.setattr(prioritise, "gemini_rank", lambda _t: (_ for _ in ()).throw(AssertionError("called")))
    db[schema.INVOICES].delete_many({})
    assert prioritise.run() == [] and db[schema.TASKS].count_documents({}) == 0


# --- invoice search (Atlas Search + Automated Embedding + Hybrid); mongomock has no $search, so "basic" mode runs

MEADOWFIELD = {"business_id": "gaia-coffee", "invoice_number": "MF-1", "direction": "payable", "counterparty": "Meadowfield Dairy",
               "amount": 54.1, "line_items": [{"description": "Whole Milk (2L)", "qty": 10, "unit_price": 1.90},
                                              {"description": "Oat Barista Milk (1L)", "qty": 6, "unit_price": 1.90},
                                              {"description": "Almond Barista Milk (1L)", "qty": 4, "unit_price": 2.20}]}


class FakeAtlas:
    """Stands in for the Atlas invoices collection: returns canned $rankFusion results and records every pipeline
    (`pipeline` = the first: the search itself; later ones are e.g. the highlight lookup)."""
    def __init__(self, docs):
        self.docs, self.pipelines = docs, []

    @property
    def pipeline(self):
        return self.pipelines[0] if self.pipelines else None

    def __getitem__(self, _name):
        return self

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return list(self.docs)

    def find(self, *_a, **_k):
        return []


def _fused(number, keyword=None, meaning=None):
    details = [{"inputPipelineName": "keyword", "rank": 1 if keyword else 0, **({"value": keyword} if keyword else {})},
               {"inputPipelineName": "meaning", "rank": 1 if meaning else 0, **({"value": meaning} if meaning else {})}]
    return {"invoice_number": number, "score": 0.03, "details": {"details": details}}


def test_every_invoice_gets_search_text_and_old_ones_are_backfilled(db):
    from backend.db import schema, search
    doc = to_invoice_doc(Extraction(**BASE, printed_total=237.0), [], 1, "pdf")
    assert doc["search_text"] == "Bow Lane Roasters | supplier bill (we pay) | BLR-1 | Espresso 1kg; Decaf 1kg"
    db[schema.INVOICES].update_many({}, {"$unset": {"search_text": ""}})
    assert search.ensure_search_indexes(db) == {}  # no Atlas Search on mongomock: indexes skipped, backfill still runs
    assert db[schema.INVOICES].count_documents({"search_text": {"$exists": False}}) == 0
    assert "Oat milk, 1L" in db[schema.INVOICES].find_one({"invoice_number": "EDC-7781"})["search_text"]


def test_search_falls_back_to_word_match_without_atlas_search(db):
    from backend.db import search
    result = search.search_invoices(db, "dairy milk")
    assert result.mode == "basic" and result.error
    assert result.hits[0]["counterparty"] == "Eastside Dairy Co" and result.hits[0]["matched_by"] == ["keyword"]
    assert search.search_invoices(db, "   ").hits == []
    assert search.search_invoices(db, "submarine").hits == []


def test_hybrid_search_keeps_keyword_hits_and_drops_weak_meaning_only_hits():
    from backend.db import search
    atlas = FakeAtlas([_fused("EDC-7781", keyword=0.6, meaning=0.64),  # exact number: weak meaning, kept by keyword
                       _fused("MF-2087", meaning=0.83),                 # strong meaning match
                       _fused("MEB-3310", meaning=0.79),                # close behind the best: kept
                       _fused("GAIA-1041", meaning=0.72),               # above 0.70 but far behind the best: dropped
                       _fused("CLS-0457", meaning=0.65)])               # vector search's leftover neighbour: dropped
    result = search.search_invoices(atlas, "milk supplier")
    assert result.mode == "hybrid"
    assert [h["invoice_number"] for h in result.hits] == ["EDC-7781", "MF-2087", "MEB-3310"]
    assert result.hits[0]["matched_by"] == ["keyword", "meaning"] and result.hits[1]["matched_by"] == ["meaning"]
    fusion = atlas.pipeline[0]["$rankFusion"]["input"]["pipelines"]
    assert fusion["meaning"][0]["$vectorSearch"]["query"] == {"text": "milk supplier"}  # Atlas embeds the query itself
    assert fusion["keyword"][0]["$search"]["compound"]["filter"] == [{"equals": {"path": "business_id", "value": "gaia-coffee"}}]


def test_typo_tolerance_only_for_longer_words():
    from backend.db.search import _keyword_clauses
    assert not any("fuzzy" in c["text"] for c in _keyword_clauses("milk"))  # "milk" must not match "Mile End"
    fuzzy = [c["text"] for c in _keyword_clauses("diary milk") if "fuzzy" in c["text"]]
    assert fuzzy and fuzzy[0]["query"] == "diary"


def test_price_check_compares_like_for_like_items_across_suppliers(db):
    from backend.db import search
    check = search.price_check(db, MEADOWFIELD)
    by_item = {c["item"]: c for c in check.comparisons}
    assert all(c["kind"] == "other_supplier" for c in check.comparisons)  # Meadowfield has no earlier bills of its own
    assert by_item["Whole Milk (2L)"]["other_price"] == 1.85 and by_item["Whole Milk (2L)"]["change_pct"] == 2.7
    assert by_item["Whole Milk (2L)"]["counterparty"] == "Eastside Dairy Co"
    assert by_item["Oat Barista Milk (1L)"]["change_pct"] == -9.5  # cheaper than Eastside's "Oat milk, 1L"
    assert by_item["Whole Milk (2L)"]["exact"] and not by_item["Oat Barista Milk (1L)"]["exact"]  # barista ≠ plain oat milk
    assert "Almond Barista Milk (1L)" not in by_item  # nothing comparable: no guess
    assert [c["item"] for c in check.cheaper_elsewhere] == ["Whole Milk (2L)"] and check.rises == []


def test_price_check_never_compares_customer_invoices_with_supplier_bills(db):
    from backend.db import search
    receivable = {**MEADOWFIELD, "direction": "receivable", "counterparty": "Some Café",
                  "line_items": [{"description": "Oat milk upgrade", "qty": 1, "unit_price": 99.0}]}
    assert all(c["invoice_number"].startswith("GAIA-") for c in search.price_check(db, receivable).comparisons)
    assert search.price_check(db, {**MEADOWFIELD, "line_items": []}).comparisons == []


def _eastside(**kw):
    return {"business_id": "gaia-coffee", "invoice_number": "EDC-9000", "direction": "payable", "counterparty": "Eastside Dairy Co.",
            "currency": "GBP", "line_items": [{"description": "Whole milk, 2L", "qty": 40, "unit_price": 1.95}], **kw}


def test_same_supplier_rise_is_price_creep_even_with_a_name_variant(db):
    from backend.db import search
    check = search.price_check(db, _eastside())  # "Eastside Dairy Co." = the seeded "Eastside Dairy Co"
    assert [(c["kind"], c["invoice_number"], c["change_pct"]) for c in check.rises] == [("same_supplier", "EDC-7781", 5.4)]


def test_different_pack_sizes_are_never_compared(db):
    from backend.db import search
    one_litre = _eastside(line_items=[{"description": "Whole milk, 1L", "qty": 40, "unit_price": 0.95}])
    assert search.price_check(db, one_litre).comparisons == []  # was a false "-49%" against the 2L carton
    assert search._sizes("Whole Milk (2L)") == search._sizes("whole milk, 2 litres") == {"2l"}


def test_only_earlier_trusted_same_currency_positive_prices_count(db):
    from backend.db import schema, search
    old = datetime.now(timezone.utc) - timedelta(days=200)
    assert search.price_check(db, _eastside(issue_date=old)).comparisons == []  # every Eastside bill is newer
    assert search.price_check(db, _eastside(currency="EUR")).comparisons == []
    free = _eastside(line_items=[{"description": "Whole milk, 2L", "qty": 1, "unit_price": 0.0}])
    assert search.price_check(db, free).comparisons == []
    flagged = search.price_check(db, _eastside(needs_review=True))
    assert flagged.comparisons == [] and "check" in flagged.skipped
    db[schema.INVOICES].update_one({"invoice_number": "EDC-7781"}, {"$set": {"needs_review": True}})
    assert search.price_check(db, _eastside()).rises[0]["invoice_number"] == "EDC-7764"  # misread bill skipped


def test_ties_go_to_the_most_similar_invoice(db, monkeypatch):
    from backend.db import search
    hits = [{"_id": 1, "invoice_number": "MOST-SIMILAR", "counterparty": "A", "currency": "GBP",
             "issue_date": datetime(2026, 1, 1, tzinfo=timezone.utc), "line_items": [{"description": "Whole milk, 2L", "unit_price": 1.80}]},
            {"_id": 2, "invoice_number": "LESS-SIMILAR", "counterparty": "B", "currency": "GBP",
             "issue_date": datetime(2026, 1, 1, tzinfo=timezone.utc), "line_items": [{"description": "Whole milk, 2L", "unit_price": 1.20}]}]
    monkeypatch.setattr(search, "similar_invoices", lambda *_a, **_k: search.SearchResult(hits, "hybrid"))
    check = search.price_check(db, _eastside(counterparty="Somebody New", counterparty_key="somebodynew"))
    assert [c["invoice_number"] for c in check.comparisons] == ["MOST-SIMILAR"]


class EmptyAtlas:
    """Atlas with a missing/building index: $search answers with nothing (no error). Everything else is the real mock."""
    def __init__(self, db):
        self.db = db

    def __getitem__(self, name):
        coll = self.db[name]
        return type("Coll", (), {"aggregate": lambda _s, _p: [], "__getattr__": lambda _s, a: getattr(coll, a)})()


def test_an_empty_atlas_answer_falls_back_and_is_logged_once_an_hour(db):
    from backend.db import schema, search
    first = search.search_invoices(EmptyAtlas(db), "dairy")
    assert first.mode == "basic" and first.hits[0]["counterparty"] == "Eastside Dairy Co" and "building" in first.error
    search.search_invoices(EmptyAtlas(db), "cups")
    search.search_invoices(db, "milk")  # mongomock: $search unsupported, also a fallback
    assert db[schema.AGENT_LOG].count_documents({"kind": "search_fallback"}) == 1
    assert search.search_invoices(EmptyAtlas(db), "submarine").hits == []  # genuinely nothing: still empty


def test_queries_without_words_or_too_long_are_handled():
    from backend.db import search
    atlas = FakeAtlas([])
    assert search.search_invoices(atlas, "?!..").hits == [] and atlas.pipeline is None  # no Atlas call at all
    search.search_invoices(atlas, "milk " * 500)
    assert len(atlas.pipeline[0]["$rankFusion"]["input"]["pipelines"]["meaning"][0]["$vectorSearch"]["query"]["text"]) <= 200


# --- supplier loyalty + price check in the pipeline and priorities

def _today_str():
    return datetime.now(timezone.utc).date().isoformat()


def _eastside_bill(fake_extraction, price=1.95, number="EDC-9100", **over):
    from tests.conftest import make_invoice
    from backend.ai.extract import ExtractedLineItem as L
    fake_extraction(make_invoice(**{"invoice_number": number, "issue_date": _today_str(), "printed_total": round(40 * price, 2),
                                    "line_items": [L(description="Whole milk, 2L", qty=40, unit_price=price)], **over}))
    return ingest_invoice(b"%PDF", "application/pdf", "email", origin="email from billing@eastsidedairy.example.com")


def test_loyal_supplier_context_and_wording_ask_to_match_never_switch(db):
    from backend.db import search
    check = search.price_check(db, {**MEADOWFIELD, "counterparty": "Eastside Dairy Co", "counterparty_key": "eastsidedairyco",
                                    "line_items": [{"description": "Whole milk, 2L", "qty": 40, "unit_price": 1.95}]})
    assert check.supplier["bills"] == 5 and check.supplier["paid"] == 4 and check.supplier["paid_on_time"] == 4
    line = check.lines()[0]
    assert "put Whole milk, 2L up 5.4% (£1.85 → £1.95 since EDC-7781)" in line
    db[schema.INVOICES].insert_one({**MEADOWFIELD, "counterparty_key": "meadowfielddairy", "currency": "GBP",
                                    "issue_date": datetime.now(timezone.utc) - timedelta(days=3),
                                    "line_items": [{"description": "Whole Milk (2L)", "qty": 10, "unit_price": 1.70}]})
    lines = search.price_check(db, {**MEADOWFIELD, "counterparty": "Eastside Dairy Co", "counterparty_key": "eastsidedairyco",
                                    "line_items": [{"description": "Whole milk, 2L", "qty": 40, "unit_price": 1.95}]}).lines()
    match = next(ln for ln in lines if "match" in ln)
    assert "Meadowfield Dairy charged £1.70" in match and "worth asking Eastside Dairy Co to match" in match
    assert "vs £1.95 here:" in match  # same item: no "similar" caveat
    assert "5 bills since" in match and "all paid on time)" in match  # EDC-7781 is unpaid but not due yet
    assert "switch" not in " ".join(lines).lower()
    db[schema.INVOICES].update_one({"invoice_number": "EDC-7781"}, {"$set": {"due_date": datetime.now(timezone.utc) - timedelta(days=2)}})
    late = search.price_check(db, {**MEADOWFIELD, "counterparty": "Eastside Dairy Co", "counterparty_key": "eastsidedairyco",
                                   "line_items": [{"description": "Whole milk, 2L", "qty": 40, "unit_price": 1.95}]}).lines()
    assert "all paid on time, 1 overdue now)" in next(ln for ln in late if "match" in ln)  # loyalty cuts both ways


def test_preferred_supplier_gets_rises_reported_but_no_alternatives(db):
    from backend.db import search
    db[schema.INVOICES].insert_one({**MEADOWFIELD, "counterparty_key": "meadowfielddairy", "currency": "GBP",
                                    "issue_date": datetime.now(timezone.utc) - timedelta(days=3),
                                    "line_items": [{"description": "Whole Milk (2L)", "qty": 10, "unit_price": 1.70}]})
    bill = {**MEADOWFIELD, "counterparty": "Eastside Dairy Co", "counterparty_key": "eastsidedairyco",
            "line_items": [{"description": "Whole milk, 2L", "qty": 40, "unit_price": 1.95}]}
    assert search.price_check(db, bill).cheaper_elsewhere
    assert search.set_preferred(db, "Eastside Dairy Co.") == ["eastsidedairyco"]
    check = search.price_check(db, bill)
    assert check.preferred and check.rises and check.cheaper_elsewhere == []
    assert not any("match" in ln for ln in check.lines())
    assert search.set_preferred(db, "Eastside Dairy Co", preferred=False) == []


def test_new_supplier_bill_is_price_checked_on_arrival_and_logged(db, fake_extraction):
    r = _eastside_bill(fake_extraction)
    assert r.created and "Whole milk, 2L up 5.4%" in r.message
    log = db[schema.AGENT_LOG].find_one({"kind": "price_checked"})
    assert log and "up 5.4%" in log["summary"] and log["detail"]["comparisons"][0]["kind"] == "same_supplier"


def test_unchanged_prices_are_logged_quietly_and_not_mentioned_to_the_owner(db, fake_extraction):
    r = _eastside_bill(fake_extraction, price=1.85, number="EDC-9101")
    assert r.created and "%" not in r.message
    assert "in line" in db[schema.AGENT_LOG].find_one({"kind": "price_checked"})["summary"]


def test_a_failing_price_check_never_blocks_the_invoice(db, fake_extraction, monkeypatch):
    from backend.db import search

    def broken(*_a, **_k):
        raise RuntimeError("atlas down")
    monkeypatch.setattr(search, "price_check", broken)
    r = _eastside_bill(fake_extraction)
    assert r.ok and r.created and db[schema.INVOICES].find_one({"invoice_number": "EDC-9100"})
    assert db[schema.AGENT_LOG].find_one({"kind": "price_check_failed"})


def test_bills_needing_review_and_customer_invoices_are_not_price_checked(db, fake_extraction):
    _eastside_bill(fake_extraction, printed_total=999.0)  # total doesn't add up → needs review
    from tests.conftest import make_invoice
    fake_extraction(make_invoice(issuer_name="GAIA Coffee", recipient_name="Test Cafe", invoice_number="GAIA-4100",
                                 issue_date=_today_str()))
    ingest_invoice(b"%PDF", "application/pdf", "pdf")
    assert db[schema.AGENT_LOG].count_documents({"kind": {"$in": ["price_checked", "price_check_failed"]}}) == 0


def test_price_rise_becomes_a_ranked_task_with_negotiate_facts(db, fake_extraction):
    _eastside_bill(fake_extraction)
    tasks = [t for t in prioritise.candidates(db, datetime.now(timezone.utc)) if t["kind"].startswith("price")]
    assert [t["kind"] for t in tasks] == ["price_rise"]
    assert tasks[0]["title"] == "Ask Eastside Dairy Co about the price rise on Whole milk, 2L (+5.4%)"
    assert tasks[0]["amount"] == 4.0 and "don't switch" in tasks[0]["facts"]
    ranked = prioritise.rule_rank(tasks + [{"_id": "x", "kind": "approval", "facts": "£450 overdue", "score": 900}])
    assert ranked[0]["_id"] == "x"  # a few pounds on milk never outranks an overdue £450


def test_adding_missing_seed_invoices_only_adds(db):
    from data import seed
    db[schema.INVOICES].delete_one({"invoice_number": "EDC-7702"})
    db[schema.INVOICES].update_one({"invoice_number": "EDC-7781"}, {"$set": {"status": "paid"}})  # a change made in the demo
    invoices = seed.build_invoices(seed._today(), datetime.now(timezone.utc))
    assert seed.add_missing(db, invoices) == 1 and seed.add_missing(db, invoices) == 0
    assert db[schema.INVOICES].find_one({"invoice_number": "EDC-7781"})["status"] == "paid"  # left alone


def test_similar_but_not_identical_items_are_named_so_the_owner_judges_quality(db):
    from backend.db import search
    db[schema.INVOICES].insert_one({**MEADOWFIELD, "counterparty_key": "meadowfielddairy", "currency": "GBP",
                                    "issue_date": datetime.now(timezone.utc) - timedelta(days=3)})
    lines = search.price_check(db, {**MEADOWFIELD, "counterparty": "Eastside Dairy Co", "counterparty_key": "eastsidedairyco",
                                    "line_items": [{"description": "Oat milk, 1L", "qty": 36, "unit_price": 2.10}]}).lines()
    oat = next(ln for ln in lines if "Oat Barista" in ln)
    assert "vs £2.10 for your Oat milk, 1L (similar, not identical)" in oat


# --- weather + events storage (backend/conditions.py), inputs' formats as-is

def _wday(date="2026-10-25", **over):
    return {"pitch_id": "broadway-market", "date": date, "summary": "Overcast", "weather_code": 3, "temp_max_c": 13.4,
            "temp_min_c": 9.4, "precip_mm": 0.4, "precip_chance_pct": 98, "wind_max_kmh": 18.0, "source": "open-meteo",
            "fetched_at": "2026-10-24T17:00:00+00:00", **over}


def _event(source_id="E1", date="2026-10-25", **over):
    return {"source": "test-feed", "source_id": source_id, "name": "Broadway Market Craft Fair", "category": "Arts",
            "genre": "Market", "date": date, "starts_at": "2026-10-25T09:00:00Z", "status": "onsale", "venue": "London Fields",
            "lat": 51.54, "lon": -0.06, "distance_km": 0.6, "pitch_id": "broadway-market", "url": None,
            "fetched_at": "2026-10-24T17:00:00Z", **over}


def test_weather_is_stored_once_per_pitch_and_day_and_updated_when_the_forecast_changes(db):
    from backend import conditions
    first = conditions.record_weather([_wday(), _wday("2026-10-26", summary="Heavy drizzle", weather_code=55)])
    assert (first.new, first.updated, first.unchanged) == (2, 0, 0)
    again = conditions.record_weather([_wday(fetched_at="2026-10-24T18:00:00Z")])  # same forecast, fetched later
    assert (again.new, again.updated, again.unchanged) == (0, 0, 1)
    better = conditions.record_weather([_wday(summary="Light rain", weather_code=61, precip_chance_pct=100)])
    assert better.updated == 1 and db[schema.WEATHER].count_documents({}) == 2
    assert db[schema.WEATHER].find_one({"date": "2026-10-25"})["summary"] == "Light rain"
    assert db[schema.AGENT_LOG].count_documents({"kind": "weather_synced"}) == 2  # nothing logged when nothing changed


def test_bad_weather_days_are_rejected_and_the_rest_still_stored(db):
    from backend import conditions
    r = conditions.record_weather([_wday(), _wday("25/10/2026"), _wday(pitch_id=""), _wday("2026-10-27", temp_max_c="warm"),
                                   _wday("2026-10-28", weather_code=True), "junk",
                                   _wday("2026-10-29", temp_max_c=None, precip_chance_pct=None)])  # gaps are fine
    assert r.new == 2 and len(r.errors) == 5
    assert db[schema.WEATHER].find_one({"date": "2026-10-29"})["temp_max_c"] is None
    dup = conditions.record_weather([_wday("2026-10-30"), _wday("2026-10-30", summary="Clear")])  # same day twice
    assert dup.new == 1 and db[schema.WEATHER].find_one({"date": "2026-10-30"})["summary"] == "Clear"


def test_events_are_idempotent_keep_the_nearest_pitch_and_survive_a_garbled_start_time(db):
    from backend import conditions
    r = conditions.record_events([_event(), _event(pitch_id="victoria-park", distance_km=2.4),
                                  _event("E2", starts_at="soon", name="Gig")])
    assert r.new == 2 and r.errors == []
    e1 = db[schema.EVENTS].find_one({"source_id": "E1"})
    assert e1["pitch_id"] == "broadway-market" and e1["distance_km"] == 0.6 and e1["synthetic"] is False
    assert e1["search_text"] == "Broadway Market Craft Fair | Arts, Market | London Fields"
    assert db[schema.EVENTS].find_one({"source_id": "E2"})["starts_at"] is None  # time TBA, event kept
    again = conditions.record_events([_event(), _event("E2", starts_at="soon", name="Gig")])
    assert (again.new, again.updated, again.unchanged) == (0, 0, 2)
    assert conditions.record_events([_event(date="2026-10-26")]).updated == 1  # rescheduled
    assert db[schema.EVENTS].find_one({"source_id": "E1"})["first_seen_at"] == e1["first_seen_at"]
    assert conditions.record_events([{"source": "test-feed", "name": "No id"}]).errors


def test_events_missing_from_a_refetched_window_are_marked_removed_not_deleted(db):
    from backend import conditions
    conditions.record_events([_event("E1"), _event("E2", date="2026-10-26"), _event("E3", date="2026-11-20")])
    window = [("broadway-market", "2026-10-25", "2026-10-31")]
    r = conditions.record_events([_event("E1")], covered=window)  # E2 cancelled; E3 is outside the window
    assert r.removed == 1 and db[schema.EVENTS].count_documents({}) == 3
    assert db[schema.EVENTS].find_one({"source_id": "E2"})["status"] == "removed"
    assert db[schema.EVENTS].find_one({"source_id": "E3"})["status"] == "onsale"
    assert conditions.record_events([_event("E1")]).removed == 0  # no window given: nothing assumed gone
    back = conditions.record_events([_event("E1"), _event("E2", date="2026-10-26")], covered=window)
    assert back.updated == 1 and db[schema.EVENTS].find_one({"source_id": "E2"})["status"] == "onsale"


def test_the_forecast_reads_weather_and_live_events_per_day(db):
    from backend import conditions
    conditions.record_weather([_wday()])
    conditions.record_events([_event("E1"), _event("E2", distance_km=0.2, name="Closer"), _event("E3", date="2026-10-26")])
    conditions.record_events([_event("E1"), _event("E2", distance_km=0.2, name="Closer")],
                             covered=[("broadway-market", "2026-10-26", "2026-10-26")])  # E3 removed
    got = conditions.conditions_for(["2026-10-25", "2026-10-26", "2026-10-27"])
    assert got["2026-10-25"]["weather"]["summary"] == "Overcast"
    assert [e["name"] for e in got["2026-10-25"]["events"]] == ["Closer", "Broadway Market Craft Fair"]
    assert got["2026-10-26"]["events"] == [] and got["2026-10-27"] == {"weather": None, "events": [], "transport": []}


def test_timestamps_without_an_offset_are_london_time(db):
    from backend import conditions
    conditions.record_events([_event(starts_at="2026-07-01T09:00:00")])  # BST: 09:00 London = 08:00 UTC
    assert db[schema.EVENTS].find_one({})["starts_at"].hour == 8


# ---------------------------------------------------------------- Argon thinking level on every call

def test_every_gemini_call_sets_a_thinking_level(monkeypatch):
    import pathlib
    from backend.ai import gemini

    for path in pathlib.Path("backend").rglob("*.py"):
        src = path.read_text()
        for chunk in src.split("GenerateContentConfig(")[1:]:
            assert "thinking_config=thinking(" in chunk[:200], f"{path}: a Gemini call without a thinking level"
    assert gemini.thinking(gemini.HIGH).thinking_level == gemini.HIGH
    monkeypatch.setenv("GEMINI_THINKING", "off")  # the escape hatch: model default, no deploy needed
    assert gemini.thinking(gemini.HIGH) is None
