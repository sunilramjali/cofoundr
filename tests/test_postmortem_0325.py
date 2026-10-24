"""Post-mortem (Sun 25 Oct, ~03:30) of the board meeting, forecast reviews and fee audit: each test reproduced a bug."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend import payments, sales
from backend.ai import board, fees, prioritise, review
from backend.ai.board import Claim, Insight, Memo
from backend.db import schema
from backend.inputs import evo, fetch, weather
from backend.inputs.pos.client import PosClient
from data import make_evo_sample

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def test_board_meetings_are_rate_limited_so_nobody_can_run_up_the_gemini_bill(db, monkeypatch):
    from bridge import main
    runs = []
    monkeypatch.setattr(board, "run", lambda: runs.append(1))
    monkeypatch.setattr(main.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    web = TestClient(main.app)
    web.post("/board/run", follow_redirects=False)
    second = web.post("/board/run", follow_redirects=False)
    assert len(runs) == 1 and "minutes" in second.headers["location"].replace("%20", " ")


def test_fee_uploads_are_rate_limited_too(db, monkeypatch):
    from bridge import main
    calls = []
    monkeypatch.setattr(fees, "run", lambda *a, **k: calls.append(1) or {"problems": ["x"], "audit": None})
    web = TestClient(main.app)
    for _ in range(2):
        r = web.post("/import/fees", files={"file": ("f.png", b"img", "image/png")}, follow_redirects=False)
    assert len(calls) == 1 and "minute" in r.headers["location"].replace("%20", " ")


def test_the_board_prompt_is_capped_to_the_most_recent_lines(db, monkeypatch):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24"), origin="test", now=NOW)
    monkeypatch.setattr(board, "MAX_SALES_LINES", 100)
    ctx = board.gather(db, NOW)
    lines = ctx["sales"].splitlines()
    assert len(lines) == 101 and lines[-1].startswith("2026-10-23") and "older" in ctx["sales_note"]


def test_a_card_only_business_gets_its_takings_in_the_memo_and_they_can_be_checked(db):
    payments.record_payments(evo.parse(make_evo_sample.OUT.read_bytes()).payments, origin="test", synthetic=True, now=NOW)
    ctx = board.gather(db, NOW)
    assert "2026-10-17" in ctx["card"]
    sat = sum(p["amount_gbp"] for p in evo.parse(make_evo_sample.OUT.read_bytes()).payments if p["paid_at"] == "2026-10-17")
    claim = Claim(kind="card_takings", value=0, start="2026-10-17", end="2026-10-17", says="x")
    assert board.compute(db, claim, NOW.date()) == (round(sat, 2), None)


def test_the_board_prompt_treats_recorded_text_as_data_not_instructions():
    assert "never instructions" in board.PROMPT


def test_a_month_only_partly_in_the_export_is_not_called_under_the_minimum():
    s = fees.FeeSchedule(rates=[fees.Rate(card="debit", channel="in_person", pct=0.3, fixed_gbp=0.03)], min_monthly_gbp=10)
    few = [{"date": d, "amount": 4.0, "fee": 0.04, "kind": "sale", "region": "domestic", "card_product": "debit"}
           for d in ("2026-09-29", "2026-10-01", "2026-10-31", "2026-11-02")]
    assert [m["month"] for m in fees.audit(few, s)["below_minimum"]] == ["2026-10"]  # Sep and Nov are only partly covered


def test_an_untrusted_newer_fee_summary_withdraws_the_old_task(db):
    db[schema.FEE_AUDITS].insert_many([
        {"_id": "a", "business_id": "gaia-coffee", "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "schedule": {"provider": "P"},
         "audit": {"overcharged": [{"extra": 0.15}], "extra_total": 0.15, "checked": 9, "first_day": "x", "last_day": "y"}},
        {"_id": "b", "business_id": "gaia-coffee", "created_at": datetime(2026, 10, 20, tzinfo=timezone.utc), "schedule": {}, "audit": None}])
    assert prioritise.fee_tasks(db) == []


def test_a_date_graded_twice_counts_once_towards_the_cap(db):
    for mode in ("backtest", "live"):
        for i in range(3):
            db[schema.FORECAST_REVIEWS].insert_one({"business_id": "gaia-coffee", "date": f"2026-10-1{i}", "mode": mode, "helped": False})
    cap, why = review.earned_cap(db)
    assert "3 graded" in why and cap == review.forecast.MAX_PCT  # 3 distinct days: not enough to judge yet


def test_a_backtest_survives_the_weather_archive_being_down(db, monkeypatch):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24"), origin="test", now=NOW)
    monkeypatch.setattr(weather, "history", lambda *a, **k: (_ for _ in ()).throw(fetch.FetchError("archive down")))
    monkeypatch.setattr(review, "ask_gemini", lambda p: [])
    reviews, summary = review.backtest(db, 9, NOW)
    assert reviews and all(r["adjustment_pct"] is None for r in reviews)


def test_a_renamed_insight_on_the_recheck_counts_as_corrected(db, monkeypatch):
    owed = board.compute(db, Claim(kind="owed_to_us", value=0, says="x"), NOW.date())[0]

    def ins(title, value):
        return Insight(title=title, finding="f", action="a", claims=[Claim(kind="owed_to_us", value=value, says="owed")])
    answers = [Memo(headline="h", insights=[ins("Cash stuck", owed + 9)]), Memo(headline="h", insights=[ins("Cash stuck in catering", owed)])]
    monkeypatch.setattr(board, "ask_gemini", lambda p: (answers.pop(0), None))
    assert board.write_memo(db, NOW)["insights"][0]["status"] == "corrected"


def test_the_first_board_meeting_after_a_restart_is_never_refused(db, monkeypatch):
    """time.monotonic() starts near 0 when a machine boots (CI runner, Fly after a deploy): "never run" must mean go."""
    from bridge import main
    monkeypatch.setattr(main.time, "monotonic", lambda: 5.0)  # 5 seconds after boot
    assert main._too_soon("board", main.BOARD_EVERY) is None
    assert main._too_soon("board", main.BOARD_EVERY) > 0  # but a second one straight after is
