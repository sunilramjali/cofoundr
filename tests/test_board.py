"""Board memo (owner: Sunil, MVP2): Gemini reads everything, code recomputes every number it states. Fake Gemini,
in-memory MongoDB. Own file so it never conflicts with test_edges/test_middle."""
from datetime import datetime, timezone

import pytest

from backend import sales
from backend.ai import board
from backend.ai.board import Claim, Insight, Memo
from backend.db import schema
from backend.inputs.pos.client import PosClient

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)  # Saturday midday: Saturday's sales so far aren't "complete"
TODAY = NOW.date()


@pytest.fixture
def till(db):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24T13:00:00+01:00"), origin="test", now=NOW)
    return db


def _truth(db, **q):
    """Recompute by hand from the raw export, independent of board.compute."""
    total = 0.0
    for o in PosClient(now="2026-12-01").iter_sales(since=q["since"], until=q["until"]):
        for it in o["items"]:
            if q.get("sku") and it["sku"] != q["sku"]:
                continue
            if q.get("mod") and not any(q["mod"] in (m.get("value") or "") for m in it.get("modifiers") or []):
                continue
            total += it["quantity"]
    return total


def _claim(**kw):
    return Claim(**{"says": "x", **kw})


def test_claims_are_recomputed_from_the_database(till):
    oat = _truth(till, since="2026-10-01T00:00:00+01:00", until="2026-10-08T00:00:00+01:00", mod="Oat Milk")
    assert board.compute(till, _claim(kind="sales_units", value=0, start="2026-10-01", end="2026-10-07", modifier="Oat Milk"), TODAY) == (oat, None)
    assert board.compute(till, _claim(kind="invoice_amount", value=0, invoice_number="EDC-7781"), TODAY)[0] > 0
    price, why = board.compute(till, _claim(kind="supplier_unit_price", value=0, invoice_number="EDC-7781", item="whole milk"), TODAY)
    assert why is None and price > 0
    owed = sum(i["amount"] for i in till[schema.INVOICES].find({"direction": "receivable", "status": {"$ne": "paid"}}))
    assert board.compute(till, _claim(kind="owed_to_us", value=0), TODAY) == (round(owed, 2), None)


@pytest.mark.parametrize("claim, why", [
    (dict(kind="sales_units", start="2026-10-01", end="2026-10-24"), "isn't over yet"),  # today is incomplete
    (dict(kind="sales_units", start="2026-10-07", end="2026-10-01"), "before start"),
    (dict(kind="sales_units", start="2026-10-01", end="2026-10-07", sku="NOPE"), "no SKU"),
    (dict(kind="order_count"), "needs a start and end"),
    (dict(kind="invoice_amount", invoice_number="INV-404"), "no invoice INV-404"),
    (dict(kind="supplier_unit_price", invoice_number="EDC-7781", item="caviar"), "no line"),
])
def test_claims_that_cant_be_checked_say_why(till, claim, why):
    actual, reason = board.compute(till, _claim(value=1, **claim), TODAY)
    assert actual is None and why in reason


def test_money_must_match_to_the_penny_and_counts_to_half_a_unit():
    assert not board.close_enough(_claim(kind="sales_revenue", value=640.50), 637.30)  # what the live run let through
    assert board.close_enough(_claim(kind="sales_revenue", value=637.30), 637.299)
    assert board.close_enough(_claim(kind="sales_units", value=225), 225.4) and not board.close_enough(_claim(kind="sales_units", value=226), 225)


def _insight(title, *claims):
    return Insight(title=title, finding="f", action="a", impact_gbp_per_month=10, claims=list(claims))


@pytest.fixture
def gemini(monkeypatch):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return answers.pop(0), 123456
    answers = []
    monkeypatch.setattr(board, "ask_gemini", fake)
    return answers, calls


def test_wrong_numbers_go_back_once_and_are_corrected_withdrawn_or_shown_failed(till, gemini):
    answers, calls = gemini
    owed = board.compute(till, _claim(kind="owed_to_us", value=0), TODAY)[0]
    good = _claim(kind="owed_to_us", value=owed, says="owed £X")
    bad = _claim(kind="owed_to_us", value=owed + 100, says="owed £X+100")
    answers += [Memo(headline="h", insights=[_insight("Right", good), _insight("Fixable", bad), _insight("Dropped", bad),
                                             _insight("Stubborn", bad), _insight("No numbers")]),
                Memo(headline="h", insights=[_insight("Fixable", good), _insight("Stubborn", bad), _insight("No numbers")])]
    doc = board.write_memo(till, NOW)
    status = {i["title"]: i["status"] for i in doc["insights"]}
    assert status == {"Right": "verified", "Fixable": "corrected", "Dropped": "withdrawn", "Stubborn": "failed", "No numbers": "failed"}
    fixable = next(i for i in doc["insights"] if i["title"] == "Fixable")
    assert "the database says" in fixable["first_problems"][0] and fixable["problems"] == []
    assert len(calls) == 2 and "owed £X+100" in calls[1] and "Right:" not in calls[1].split("database check found")[1]
    assert doc["read"]["tokens"] == 123456 and doc["read"]["sales_lines"] > 1000 and doc["synthetic"] is True


def test_the_recheck_shows_gemini_its_memo_with_pound_signs_not_escapes(till, gemini):
    # live: the memo went back as £276.00 and Gemini returned "ɶ.00" (the escape merged with the digits)
    answers, calls = gemini
    owed = board.compute(till, _claim(kind="owed_to_us", value=0), TODAY)[0]
    bad = _claim(kind="owed_to_us", value=owed + 100, says="£276.00 owed")
    answers += [Memo(headline="h", insights=[_insight("Owed", bad)]), Memo(headline="h", insights=[])]
    board.write_memo(till, NOW)
    memo_part = calls[1].split("database check found")[0]
    assert "£276.00 owed" in memo_part and "\\u00a3" not in calls[1]


def test_the_prompt_holds_every_complete_day_and_nothing_from_today(till, gemini):
    answers, calls = gemini
    answers.append(Memo(headline="h", insights=[]))
    board.write_memo(till, NOW)
    assert "2026-10-23 " in calls[0] and "2026-10-24 " not in calls[0].split("=== INVOICES")[0]
    assert "Oat Milk" in calls[0] and "EDC-7781" in calls[0] and "synthetic demo data" in calls[0]


def test_run_stores_the_memo_and_logs_what_it_read(till, gemini):
    answers, _ = gemini
    owed = board.compute(till, _claim(kind="owed_to_us", value=0), TODAY)[0]
    answers.append(Memo(headline="h", insights=[_insight("Right", _claim(kind="owed_to_us", value=owed))]))
    board.run(now=NOW)
    assert till[schema.BOARD_MEMOS].find_one()["insights"][0]["status"] == "verified"
    assert "1 insight(s) checked" in till[schema.AGENT_LOG].find_one({"kind": "board_memo"})["summary"]


def test_gemini_down_means_no_memo_and_a_logged_reason(till, gemini_down):
    assert board.run(now=NOW) is None and till[schema.BOARD_MEMOS].count_documents({}) == 0
    assert "Board memo: Gemini unavailable (429)" in till[schema.AGENT_LOG].find_one({"kind": "gemini_fallback"})["summary"]


def test_a_failed_recheck_keeps_the_first_answer_marked_failed(till, monkeypatch):
    owed = board.compute(till, _claim(kind="owed_to_us", value=0), TODAY)[0]
    first = Memo(headline="h", insights=[_insight("Wrong", _claim(kind="owed_to_us", value=owed + 1))])
    calls = []

    def flaky(prompt):
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError()
        return first, None
    monkeypatch.setattr(board, "ask_gemini", flaky)
    doc = board.write_memo(till, NOW)
    assert doc["insights"][0]["status"] == "failed" and "recheck unavailable" in doc["insights"][0]["problems"][-1]


def test_the_board_page_shows_each_status_and_strikes_out_what_failed(till, gemini):
    from fastapi.testclient import TestClient
    from bridge.main import app
    answers, _ = gemini
    owed = board.compute(till, _claim(kind="owed_to_us", value=0), TODAY)[0]
    answers += [Memo(headline="Cash is stuck in catering", insights=[
        _insight("Chase catering", _claim(kind="owed_to_us", value=owed, says="owed to us")),
        _insight("Made-up trend", _claim(kind="owed_to_us", value=owed + 50, says="a wrong total"))]),
        Memo(headline="h", insights=[_insight("Made-up trend", _claim(kind="owed_to_us", value=owed + 50, says="a wrong total"))])]
    board.run(now=NOW)
    page = TestClient(app).get("/board").text
    assert "Cash is stuck in catering" in page and "Checked ✓" in page and "Failed the check" in page
    assert "<s>Made-up trend</s>" in page and "the database says" in page and "tokens read in one Gemini call" not in page  # stored, not shown to the owner
    empty = TestClient(app).get("/board")
    assert empty.status_code == 200
