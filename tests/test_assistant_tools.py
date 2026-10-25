"""The assistant's new tools (Sunil, in Jade's backend/outputs/ area): price what-ifs, stock for a day, conditions for a
day, and propose_reminder, its one writing tool (a pending draft under the overdue check's rules, nothing sent).
In-memory DB, fake Gemini."""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend import sales
from backend.db import schema
from backend.inputs.pitches import PITCHES
from backend.inputs.pos.client import PosClient
from backend.outputs import assistant, updates
from bridge import view
from tests import test_edges

TODAY = date(2026, 10, 25)
ask = test_edges.ask  # fixture: overdue check run, approvals #1–#4 pending, fake Gemini


@pytest.fixture
def fns(db, monkeypatch):
    monkeypatch.setattr(assistant, "_today", lambda: TODAY)
    calls = []
    out = {f.__name__: f for f in assistant.tools(lambda n, res: calls.append(n) or res)}
    out["_calls"] = calls
    return out


@pytest.fixture
def till(db):
    pos = PosClient(now="2026-12-01")
    sales.record_orders(pos.iter_sales(until="2026-10-25T00:00:00+01:00"), origin="test",
                        now=datetime(2026, 10, 25, 9, tzinfo=timezone.utc))
    for c in pos.list_stock_counts():
        if c["counted_at"] < "2026-10-25":
            sales.record_stock_count(c)


def test_what_if_price_does_the_sums_from_the_till(db, till, fns):
    r = fns["what_if_price"]("flat white", 20)
    since = datetime(2026, 9, 27, tzinfo=timezone.utc) - timedelta(hours=1)  # 28 London days back, BST
    sold = sum(s["qty"] for s in db[schema.SALES].find({"meta.sku": "FLW", "ts": {"$gte": since}}))
    assert sold > 0 and r["sold_last_4_weeks"] == sold
    assert (r["product"], r["price"], r["new_price"]) == ("Flat White", 3.9, 4.1)
    assert r["extra_per_4_weeks"] == round(sold * 0.2, 2) and r["extra_per_week"] == round(sold / 4 * 0.2, 2)
    assert r["break_even_volume_change_pct"] == -4.9  # lose up to 4.9% of flat whites and still take the same
    assert "same number sold" in r["assumption"]
    assert fns["what_if_price"]("Flat White", -20)["break_even_volume_change_pct"] == 5.4  # a cut needs 5.4% more sold


def test_what_if_price_never_guesses_the_product_or_takes_silly_changes(db, till, fns):
    assert fns["what_if_price"]("Latte", 10)["product"] == "Latte"  # exact name wins over "Iced Latte"
    assert fns["what_if_price"]("matcha", 10)["did_you_mean"] == ["Hot Matcha", "Iced Matcha"]
    unknown = fns["what_if_price"]("pumpkin spice", 10)
    assert unknown["found"] is False and "Flat White" in unknown["menu"]
    for bad in (0, 500, -400):
        assert "error" in fns["what_if_price"]("Tea", bad)
    assert "whole number" in fns["what_if_price"]("Tea", "lots")["error"]


def test_what_if_price_with_no_recent_sales_says_so(db, fns):
    db[schema.PRODUCTS].insert_one({"_id": "OLD", "business_id": "gaia-coffee", "name": "Mince Pie", "price": 2.5})
    r = fns["what_if_price"]("Mince Pie", 10)
    assert r["sold_last_4_weeks"] == 0 and r["extra_per_4_weeks"] == 0 and "no Mince Pie sold" in r["note"]


def _forecast(db, day: str, revenue: float, trading: bool = True):
    db[schema.FORECASTS].insert_one({"_id": f"gaia-coffee:{day}", "business_id": "gaia-coffee", "date": day, "trading": trading,
                                     "expected_revenue": revenue, "final_revenue": revenue, "basis": "test",
                                     "adjustment": {"reason": "market nearby"} if revenue > 500 else None})


def test_stock_needed_scales_usage_by_the_forecast_and_flags_shortfalls(db, till, fns):
    _forecast(db, "2026-10-31", 600)
    _forecast(db, "2026-11-01", 400)
    r = fns["stock_needed"]("2026-10-31")
    assert r["trading"] and r["expected_takings"] == 600 and r["busier_than_usual_pct"] == 20  # 600 vs a usual 500
    est = view.stock_estimate(list(db[schema.STOCK_COUNTS].find()), datetime.now(timezone.utc))
    per_day = {x["name"]: x["per_day"] for x in est["rows"] if x["per_day"]}
    assert r["short"] and {i["item"] for i in r["short"]} <= set(per_day)
    for i in r["short"]:
        assert i["day_needs"] == round(per_day[i["item"]] * 1.2, 1)
        assert i["short_by"] > 0 and abs(i["short_by"] - (i["day_needs"] - i["on_truck_by_then"])) < 0.11  # each rounded to 0.1
    assert [i["short_by"] for i in r["short"]] == sorted((i["short_by"] for i in r["short"]), reverse=True)
    assert len(r["short"]) + r["enough_of"] == len(per_day)
    assert "estimate" in r["basis"] and "deliveries aren't recorded" in r["basis"]


def test_stock_needed_refuses_what_it_cannot_know(db, fns):
    assert "no stock counts" in fns["stock_needed"]("2026-10-31")["error"]
    assert fns["stock_needed"]("Saturday")["error"] == "date must be YYYY-MM-DD"
    assert fns["stock_needed"]("2026-10-01")["error"] == "only the next 14 days"


def test_stock_needed_without_a_forecast_never_assumes_a_trading_day(db, till, fns):
    assert "no forecast for that day" in fns["stock_needed"]("2026-11-01")["error"]  # a Sunday: GAIA may be closed


def test_stock_needed_on_a_closed_day(db, till, fns):
    _forecast(db, "2026-10-26", 0, trading=False)
    assert fns["stock_needed"]("2026-10-26") == {"date": "2026-10-26", "trading": False, "basis": "test"}


def test_day_conditions_gathers_weather_events_tfl_and_the_forecast(db, fns):
    pitch = PITCHES[0]["id"]
    db[schema.WEATHER].insert_one({"business_id": "gaia-coffee", "pitch_id": pitch, "date": "2026-10-31", "summary": "Sunny",
                                   "temp_max_c": 16.0, "precip_chance_pct": 5})
    db[schema.EVENTS].insert_many([
        {"business_id": "gaia-coffee", "date": "2026-10-31", "name": "Halloween Market", "venue": "London Fields",
         "distance_km": 0.4, "status": "onsale", "pitch_id": pitch},
        {"business_id": "gaia-coffee", "date": "2026-10-31", "name": "Cancelled gig", "distance_km": 0.1, "status": "removed",
         "pitch_id": pitch}])
    _forecast(db, "2026-10-31", 600)
    r = fns["day_conditions"]("2026-10-31")
    assert r["weather"]["summary"] == "Sunny" and r["weather"]["temp_max_c"] == 16.0
    assert [e["name"] for e in r["events"]] == ["Halloween Market"]  # removed events left out
    assert r["transport"] == [] and r["forecast"]["why"] == "market nearby"
    empty = fns["day_conditions"]("2026-11-05")
    assert empty["weather"] is None and empty["events"] == [] and empty["forecast"] is None
    assert "error" in fns["day_conditions"]("next week")


# ---------------------------------------------------------------- propose_reminder: the one tool that writes

def _invoice(db, number):
    return db[schema.INVOICES].find_one({"invoice_number": number})


def _approval(db, number):
    return db[schema.APPROVALS].find_one({"invoice_number": number, "status": "pending"})


def test_propose_reminder_never_duplicates_a_waiting_draft(db, ask, fns):
    waiting = _approval(db, "GAIA-1041")
    r = fns["propose_reminder"]("gaia-1041 ")
    assert (r["status"], r["ref"], r["reply"]) == ("already_pending", waiting["ref"], f"approve {waiting['ref']}")
    assert r["counterparty"] == "Hackney Hub Coworking" and db[schema.APPROVALS].count_documents({"status": "pending"}) == 4


def test_propose_reminder_drafts_one_pending_approval_and_touches_nothing_else(db, ask, fns):
    from backend import approvals
    approvals.decide(_approval(db, "GAIA-1041"), "reject", via="dashboard")  # owner said no earlier…
    db[schema.INVOICES].update_one({"invoice_number": "CLS-0457"}, {"$set": {"status": "open"}})  # past due, not flagged yet
    before = {i["invoice_number"]: i for i in db[schema.INVOICES].find({}, {"_id": 0})}
    others = {k: v for k, v in before.items() if k != "GAIA-1041"}

    r = fns["propose_reminder"]("GAIA-1041")  # …and now asks to chase it
    assert r["status"] == "drafted" and r["tone"] == "friendly nudge" and r["reply"] == f"approve {r['ref']}"
    a = _approval(db, "GAIA-1041")
    assert a["ref"] == r["ref"] and a["action_type"] == "invoice_reminder" and a["decided_via"] is None
    assert db[schema.APPROVALS].count_documents({"status": "pending"}) == 4  # 3 left + this one
    after = {i["invoice_number"]: i for i in db[schema.INVOICES].find({}, {"_id": 0}) if i["invoice_number"] != "GAIA-1041"}
    assert after == others  # CLS-0457 still "open": the one-invoice check didn't flag anything else
    assert _invoice(db, "GAIA-1041")["reminders_sent"] == 0  # drafted, not sent
    log = db[schema.AGENT_LOG].find_one({"kind": "overdue_check", "detail.refs": [r["ref"]]})
    assert log  # so the WhatsApp pusher announces it
    assert f"approve {r['ref']}" in updates.get_updates(consumer="test", peek=True)["text"]


@pytest.mark.parametrize("number, change, status", [
    ("GAIA-1045", {}, "not_overdue"),
    ("EDC-7781", {}, "supplier_bill"),
    ("GAIA-1029", {}, "paid"),
    ("GAIA-9999", {}, "not_found"),
    ("GAIA-1032", {"reminders_sent": 3}, "max_reminders"),
    ("GAIA-1038", {"last_reminder_at": "now"}, "reminded_recently"),
    ("GAIA-1038", {"needs_review": True}, "needs_review"),
])
def test_propose_reminder_keeps_every_rule(db, ask, fns, number, change, status):
    from backend import approvals
    for n in ("GAIA-1032", "GAIA-1038"):
        if _approval(db, n):
            approvals.decide(_approval(db, n), "reject", via="dashboard")
    if change.get("last_reminder_at") == "now":
        change = {"last_reminder_at": datetime.now(timezone.utc) - timedelta(days=1)}
    if change:
        db[schema.INVOICES].update_one({"invoice_number": number}, {"$set": change})
    pending = db[schema.APPROVALS].count_documents({"status": "pending"})
    r = fns["propose_reminder"](number)
    assert r["status"] == status and r["why"]
    assert db[schema.APPROVALS].count_documents({"status": "pending"}) == pending  # refused → nothing drafted


def test_chase_by_chat_drafts_but_never_claims_it_sent(db, ask):
    from backend import approvals
    approvals.decide(_approval(db, "GAIA-1041"), "reject", via="dashboard")

    def turn(tools):
        r = tools["propose_reminder"]("GAIA-1041")
        return f"Drafted a friendly nudge to {r['counterparty']}. Reply “approve {r['ref']}” to send it."
    ask(turn)
    r = assistant.answer("chase Hackney Hub")
    assert r.by == "gemini" and r.verified and r.tools == ["propose_reminder"] and "Drafted" in r.text
    ref = _approval(db, "GAIA-1041")["ref"]
    assert f"approve {ref}" in r.text

    approvals.decide(_approval(db, "GAIA-1041"), "reject", via="dashboard")
    ask(lambda tools: (tools["propose_reminder"]("GAIA-1041"), "I've sent the reminder to Hackney Hub.")[1])
    lie = assistant.answer("chase Hackney Hub again")
    again = _approval(db, "GAIA-1041")["ref"]
    assert lie.text == ("I can't approve, pay or send anything myself: I only drafted it, nothing was sent. "
                        f"Reply “approve {again}” to send it.")  # a draft is never "sent", and never "nothing changed"
