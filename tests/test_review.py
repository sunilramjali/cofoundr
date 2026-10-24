"""Forecast reviews (owner: Sunil, MVP2): grading against actual takings, checked lessons, earned trust, backtest
without peeking. Fake Gemini and fake Open-Meteo, in-memory MongoDB. Own file: no conflicts with test_edges/test_middle."""
from datetime import date, datetime, timezone

import pytest

from backend import sales
from backend.ai import forecast, review
from backend.ai.forecast import DayAdjustment, Factor
from backend.ai.review import Lesson
from backend.db import schema
from backend.inputs import fetch, weather
from backend.inputs.pos.client import PosClient

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)


def _fc(day="2026-10-17", expected=600.0, pct=None, kind="weather"):
    adj = None if pct is None else {"pct": pct, "factors": [{"kind": kind, "label": "Rain", "effect_pct": pct}]}
    return {"date": day, "weekday": "Saturday", "expected_revenue": expected,
            "final_revenue": round(expected * (1 + (pct or 0) / 100), 2), "adjustment": adj, "synthetic": True}


# --- grading (code only)

def test_grading_measures_whether_the_adjustment_helped():
    hurt = review.grade(_fc(pct=-20), actual=600, mode="live", now=NOW)
    assert hurt["baseline_error_pct"] == 0.0 and hurt["final_error_pct"] == 20.0 and hurt["helped"] is False
    helped = review.grade(_fc(pct=-10), actual=540, mode="live", now=NOW)
    assert helped["helped"] is True and helped["final_error_pct"] == 0.0
    assert review.grade(_fc(), actual=600, mode="live", now=NOW)["helped"] is None  # nothing to judge
    assert review.grade(_fc(pct=0), actual=500, mode="live", now=NOW)["helped"] is None  # "no change" isn't a bet


def _reviews(*pairs, kind="weather"):
    """(pct, actual) per day → graded reviews on consecutive dates."""
    return [review.grade(_fc(day=f"2026-10-{10 + i:02d}", pct=pct, kind=kind), actual, "backtest", NOW) for i, (pct, actual) in enumerate(pairs)]


def test_lessons_must_agree_with_the_measured_numbers():
    worse = review.stats(_reviews((-20, 600), (-15, 610), (-10, 590)))  # rain cuts made every day worse
    dates = worse["weather"]["dates"]
    assert review.check_lesson(Lesson(text="Cut less for rain.", about="weather", direction="adjust_less", evidence_dates=dates[:2]), worse) == []
    bad = review.check_lesson(Lesson(text="Cut more for rain.", about="weather", direction="adjust_more", evidence_dates=dates), worse)
    assert any("contradicts the numbers" in p for p in bad)
    made_up = review.check_lesson(Lesson(text="x", about="weather", direction="adjust_less", evidence_dates=["2026-09-01"]), worse)
    assert any("aren't graded days" in p for p in made_up)
    assert "not enough graded days" in review.check_lesson(Lesson(text="x", about="event", direction="keep", evidence_dates=dates), worse)[0]
    wordy = review.check_lesson(Lesson(text="word " * 60, about="weather", direction="adjust_less", evidence_dates=dates), worse)
    assert any("one short sentence" in p for p in wordy)


@pytest.mark.parametrize("helped_of_10, cap", [(7, 35.0), (5, 20.0), (2, 10.0)])
def test_the_cap_is_earned_from_the_track_record(db, helped_of_10, cap):
    for i in range(10):
        db[schema.FORECAST_REVIEWS].insert_one({"business_id": "gaia-coffee", "date": f"2026-10-{10 + i:02d}", "helped": i < helped_of_10})
    got, why = review.earned_cap(db)
    assert got == cap and f"helped on {helped_of_10} of the last 10" in why


def test_too_short_a_record_keeps_the_full_cap(db):
    db[schema.FORECAST_REVIEWS].insert_many([{"business_id": "gaia-coffee", "date": "2026-10-1%d" % i, "helped": False} for i in range(3)])
    assert review.earned_cap(db)[0] == forecast.MAX_PCT


def test_the_earned_cap_is_enforced_by_the_forecast_check():
    fc = forecast.baseline({}, {}, {}, date(2026, 10, 24), days=1)[0]
    refs = forecast.day_conditions("2026-10-24", {"weather": {"summary": "Rain", "weather_code": 63}, "events": [], "transport": []})
    cut = DayAdjustment(date="2026-10-24", pct=-18, reason="-18% Sat: rain", confidence="high",
                        factors=[Factor(kind="weather", ref="weather:2026-10-24", effect_pct=-18, why="rain")])
    assert forecast.check(fc, refs, cut) == [] and any("beyond ±10%" in p for p in forecast.check(fc, refs, cut, max_pct=10))


def test_observed_rain_counts_only_during_trading_hours():
    assert not forecast.is_wet({"weather_code": 55, "precip_mm": 4.5, "trading_rain_mm": 0.0})  # it rained at night
    assert forecast.is_wet({"weather_code": 61, "precip_mm": 5.3, "trading_rain_mm": 4.6})


# --- lessons stored, fed to the next forecast, and Gemini down

@pytest.fixture
def lessons_from(monkeypatch):
    def use(*lessons):
        monkeypatch.setattr(review, "ask_gemini", lambda prompt: list(lessons))
    return use


def test_accepted_lessons_replace_the_old_ones_and_reach_the_next_forecast(db, lessons_from, monkeypatch):
    graded = _reviews((-20, 600), (-15, 610), (-10, 590))
    dates = review.stats(graded)["weather"]["dates"]
    lessons_from(Lesson(text="Rain barely dents a market day: cut less.", about="weather", direction="adjust_less", evidence_dates=dates),
                 Lesson(text="Cut more.", about="weather", direction="adjust_more", evidence_dates=dates))
    db[schema.LESSONS].insert_one({"business_id": "gaia-coffee", "text": "old lesson", "active": True, "created_at": NOW})
    accepted, rejected = review.learn(db, graded, NOW)
    assert [a["text"] for a in accepted] == ["Rain barely dents a market day: cut less."] and len(rejected) == 1
    assert review.current_lessons(db) == ["Rain barely dents a market day: cut less."]
    prompts = []
    monkeypatch.setattr(forecast, "ask_gemini", lambda p: prompts.append(p) or [])
    fc = forecast.baseline({date(2026, 10, 17): {"revenue": 600, "orders": 9}}, {}, {}, date(2026, 10, 24), days=1)
    fc[0]["trading"] = True
    forecast.adjust(fc, {"2026-10-24": {"weather": {"summary": "Rain", "weather_code": 63}, "events": [], "transport": []}},
                    {}, lessons=review.current_lessons(db), max_pct=20)
    assert "Rain barely dents a market day" in prompts[0] and "within ±20%" in prompts[0]


def test_gemini_down_keeps_the_grades_and_logs_why(db, gemini_down):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24"), origin="test", now=NOW)
    db[schema.FORECASTS].insert_one({"_id": "gaia-coffee:2026-10-17", "business_id": "gaia-coffee", **_fc(pct=-20), "trading": True})
    result = review.run(now=NOW)
    assert result["graded"] == 1 and result["error"] == "429" and db[schema.FORECAST_REVIEWS].count_documents({}) == 1
    assert db[schema.AGENT_LOG].find_one({"kind": "gemini_fallback"}) and db[schema.LESSONS].count_documents({}) == 0


def test_grading_stored_forecasts_is_idempotent_and_skips_days_without_takings(db, lessons_from):
    lessons_from()
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24"), origin="test", now=NOW)
    db[schema.FORECASTS].insert_many([
        {"_id": "gaia-coffee:2026-10-17", "business_id": "gaia-coffee", **_fc(), "trading": True},
        {"_id": "gaia-coffee:2026-10-18", "business_id": "gaia-coffee", **_fc(day="2026-10-18"), "trading": True},  # Sunday: no sales
        {"_id": "gaia-coffee:2026-10-25", "business_id": "gaia-coffee", **_fc(day="2026-10-25"), "trading": True}])  # future
    review.run(now=NOW)
    review.run(now=NOW)
    assert [r["date"] for r in db[schema.FORECAST_REVIEWS].find()] == ["2026-10-17"]


# --- backtest: observed weather, no peeking

def test_backtest_replays_with_observed_weather_and_never_shows_gemini_the_answers(db, monkeypatch, lessons_from):
    lessons_from()
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until="2026-10-24"), origin="test", now=NOW)
    monkeypatch.setattr(weather, "history", lambda start, end: [
        {"date": d, "summary": "Light rain", "weather_code": 61, "precip_mm": 5.0, "trading_rain_mm": 4.0}
        for d in ("2026-10-16", "2026-10-17", "2026-10-19", "2026-10-21", "2026-10-23")])
    prompts = []

    def fake_adjust(prompt):
        prompts.append(prompt)
        return [DayAdjustment(date=d, pct=-10, reason="-10%: rain", confidence="low",
                              factors=[Factor(kind="weather", ref=f"weather:{d}", effect_pct=-10, why="rain")])
                for d in ("2026-10-16", "2026-10-17", "2026-10-19", "2026-10-21", "2026-10-23")]
    monkeypatch.setattr(forecast, "ask_gemini", fake_adjust)
    result = review.run(now=NOW, backtest_days=9)
    graded = {r["date"]: r for r in result["reviews"]}
    assert set(graded) == {"2026-10-16", "2026-10-17", "2026-10-19", "2026-10-21", "2026-10-23"}
    assert all(r["mode"] == "backtest" and r["adjustment_pct"] == -10 for r in graded.values())
    history_part = prompts[0].split("GAIA's daily takings so far")[1]
    assert '"2026-10-14"' in history_part and not any(f'"{d}"' in history_part for d in graded)  # answers never shown
    assert "trading_rain_mm" in prompts[0]


def test_weather_history_sums_rain_only_while_the_truck_is_open(monkeypatch):
    hours = [f"2026-10-22T{h:02d}:00" for h in range(24)]
    rain = [1.0 if h in (2, 3, 21) else (0.5 if h == 12 else 0.0) for h in range(24)]
    monkeypatch.setattr(fetch, "get_json", lambda *a, **k: {
        "daily": {"time": ["2026-10-22"], "weather_code": [55], "temperature_2m_max": [14.0], "temperature_2m_min": [9.0],
                  "precipitation_sum": [3.5], "wind_speed_10m_max": [18.0]},
        "hourly": {"time": hours, "precipitation": rain}})
    day = weather.history(date(2026, 10, 22), date(2026, 10, 22))[0]
    assert day["precip_mm"] == 3.5 and day["trading_rain_mm"] == 0.5 and day["summary"] == "Heavy drizzle"


def test_the_sales_page_shows_the_record_the_cap_and_the_lessons(db):
    from fastapi.testclient import TestClient
    from bridge.main import app
    assert "No grades yet" in TestClient(app).get("/sales").text
    for i in range(6):
        db[schema.FORECAST_REVIEWS].insert_one(review.grade(_fc(day=f"2026-10-1{i}", pct=-20), 600, "backtest", NOW))
    db[schema.LESSONS].insert_one({"business_id": "gaia-coffee", "text": "Rain barely dents a market day.", "active": True,
                                   "created_at": NOW, "measured": {"days": 6}})
    page = TestClient(app).get("/sales").text
    assert "made things worse" in page and "±10%" in page and "Rain barely dents a market day." in page and "replayed" in page
