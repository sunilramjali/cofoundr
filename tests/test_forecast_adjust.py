"""Forecast adjustment (owner: Sunil, MVP2): Gemini adjusts the baseline for weather, events and transport, and code
checks every answer before it's stored. Fake Gemini, no network. Own file so it never conflicts with test_edges/test_middle."""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend import conditions, sales
from backend.ai import forecast
from backend.ai.forecast import DayAdjustment, Factor
from backend.db import schema
from backend.inputs.pos.client import PosClient

SAT, MON = "2026-10-24", "2026-10-26"
RAIN = {"summary": "Heavy rain", "weather_code": 65, "temp_max_c": 11.0, "temp_min_c": 8.0, "precip_mm": 9.0,
        "precip_chance_pct": 95, "wind_max_kmh": 30.0}
SUN = {"summary": "Clear", "weather_code": 0, "temp_max_c": 17.0, "temp_min_c": 9.0, "precip_mm": 0.0,
       "precip_chance_pct": 5, "wind_max_kmh": 10.0}
CENTRAL = {"_id": "gaia-coffee:tfl:abc", "line_name": "Central", "status": "Severe Delays", "impact": "delays",
           "stations": ["Bethnal Green"], "nearest_m": 1048, "reason": "Central line: severe delays."}
FAIR = {"_id": "gaia-coffee:test-feed:E1", "name": "Broadway Market Craft Fair", "category": "Arts", "venue": "London Fields",
        "distance_km": 0.6, "starts_at": datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)}


def _forecasts():
    """Baseline for Sat 24 → Mon 26 Oct: Sat £706 avg-ish, Sun closed, Mon £606-ish."""
    end, h = date(2026, 10, 24), {}
    for w in range(1, 7):
        for wd, rev in {0: 600, 5: 700}.items():
            h[end - timedelta(days=(end.weekday() - wd) % 7 or 7) - timedelta(weeks=w - 1)] = {"revenue": rev + w, "orders": 10}
    return forecast.baseline(h, {}, {}, end, days=3), h


def _cond(sat=None, mon=None):
    return {SAT: sat or {"weather": None, "events": [], "transport": []}, "2026-10-25": {"weather": None, "events": [], "transport": []},
            MON: mon or {"weather": None, "events": [], "transport": []}}


def _day(d, pct, reason, *factors, confidence="medium"):
    return DayAdjustment(date=d, pct=pct, reason=reason, confidence=confidence,
                         factors=[Factor(kind=k, ref=r, effect_pct=e, why="because") for k, r, e in factors])


@pytest.fixture
def gemini(monkeypatch):
    """Fake Gemini: give it one answer (a list of DayAdjustment) per call; it records each prompt."""
    class Fake:
        prompts = []
        answers = []

        def __call__(self, prompt):
            self.prompts.append(prompt)
            return self.answers.pop(0)
    fake = Fake()
    fake.prompts, fake.answers = [], []
    monkeypatch.setattr(forecast, "ask_gemini", fake)
    return fake


def test_a_checked_adjustment_is_stored_with_its_factors(gemini):
    fc, h = _forecasts()
    gemini.answers = [[_day(SAT, 12, "+12% Sat: clear skies and a craft fair next door",
                            ("weather", f"weather:{SAT}", 5), ("event", FAIR["_id"], 7))]]
    out = forecast.adjust(fc, _cond(sat={"weather": SUN, "events": [FAIR], "transport": []}), h)
    sat = fc[0]
    assert out["adjusted"] == [SAT] and out["kept"] == {} and out["attempts"] == 1 and out["skipped"] == [MON]
    assert sat["final_revenue"] == round(sat["expected_revenue"] * 1.12, 2) and sat["adjustment"]["by"] == "gemini"
    assert [f["label"] for f in sat["adjustment"]["factors"]] == ["Clear", "Broadway Market Craft Fair"]
    assert sat["adjustment"]["sources"] == ["event", "weather"]
    assert fc[2]["adjustment"] is None and fc[2]["final_revenue"] == fc[2]["expected_revenue"]  # no conditions on Monday
    assert MON not in gemini.prompts[0] and "Broadway Market Craft Fair" in gemini.prompts[0]  # only days with conditions sent


def test_days_without_any_conditions_never_call_gemini(gemini):
    fc, h = _forecasts()
    out = forecast.adjust(fc, _cond(), h)
    assert gemini.prompts == [] and out["skipped"] == [SAT, MON] and all(f["adjustment"] is None for f in fc)


@pytest.mark.parametrize("bad, problem", [
    (_day(SAT, 10, "+10% Sat: festival crowds", ("event", "gaia-coffee:made-up", 10)), "not one of the conditions"),
    (_day(SAT, 8, "+8% Sat: rain brings people in", ("weather", f"weather:{SAT}", 8)), "can't raise takings"),
    (_day(SAT, 5, "+5% Sat: delays push people to walk past", ("transport", CENTRAL["_id"], 5)), "can't raise takings"),
    (_day(SAT, -20, "-20% Sat: rain", ("weather", f"weather:{SAT}", -12)), "add up to"),
    (_day(SAT, -40, "-40% Sat: washout", ("weather", f"weather:{SAT}", -25), ("transport", CENTRAL["_id"], -15)), "beyond ±35%"),
    (_day(SAT, -12, "Sat: rain all day", ("weather", f"weather:{SAT}", -12)), "doesn't state"),
    (_day(SAT, -12, "-12% Sat: rain", ("event", f"weather:{SAT}", -12)), "is weather, not event"),
])
def test_each_check_rejects_a_bad_answer(bad, problem):
    fc, _ = _forecasts()
    refs = forecast.day_conditions(SAT, {"weather": RAIN, "events": [], "transport": [CENTRAL]})
    assert any(problem in p for p in forecast.check(fc[0], refs, bad))


def test_a_rejected_day_is_sent_back_with_the_problems_and_fixed(gemini):
    fc, h = _forecasts()
    rain = {"weather": RAIN, "events": [], "transport": [CENTRAL]}
    gemini.answers = [
        [_day(SAT, 5, "+5% Sat: cosy rain", ("weather", f"weather:{SAT}", 5)),  # rain can't raise takings
         _day(MON, -10, "-10% Mon: rain", ("weather", f"weather:{MON}", -10))],  # fine
        [_day(SAT, -15, "-15% Sat: heavy rain and Central line delays", ("weather", f"weather:{SAT}", -10),
              ("transport", CENTRAL["_id"], -5)),
         _day(MON, 20, "+20% Mon: changed my mind", ("weather", f"weather:{MON}", 20))],  # already accepted: ignored
    ]
    out = forecast.adjust(fc, _cond(sat=rain, mon={"weather": RAIN, "events": [], "transport": []}), h)
    assert sorted(out["adjusted"]) == [SAT, MON] and out["attempts"] == 2 and out["kept"] == {}
    assert fc[0]["adjustment"]["pct"] == -15 and fc[0]["adjustment"]["attempts"] == 2
    assert fc[2]["adjustment"]["pct"] == -10  # Monday kept its first, checked answer
    assert "can't raise takings" in gemini.prompts[1] and f"- {SAT}:" in gemini.prompts[1] and f"- {MON}:" not in gemini.prompts[1]


def test_a_day_gemini_leaves_out_is_asked_for_again(gemini):
    fc, h = _forecasts()
    gemini.answers = [[_day(SAT, -10, "-10% Sat: rain", ("weather", f"weather:{SAT}", -10))],
                      [_day(MON, 0, "Mon: no change")]]
    out = forecast.adjust(fc, _cond(sat={"weather": RAIN, "events": [], "transport": []},
                                    mon={"weather": SUN, "events": [], "transport": []}), h)
    assert sorted(out["adjusted"]) == [SAT, MON] and "no answer for this day" in gemini.prompts[1]
    assert fc[2]["adjustment"]["pct"] == 0 and fc[2]["adjustment"]["sources"] == [] and fc[2]["final_revenue"] == fc[2]["expected_revenue"]


def test_failing_twice_keeps_the_baseline_and_says_why(gemini):
    fc, h = _forecasts()
    wrong = [_day(SAT, 10, "+10% Sat: festival", ("event", "gaia-coffee:made-up", 10))]
    gemini.answers = [wrong, wrong]
    out = forecast.adjust(fc, _cond(sat={"weather": RAIN, "events": [], "transport": []}), h)
    assert out["adjusted"] == [] and "not one of the conditions" in out["kept"][SAT][0] and out["attempts"] == 2
    assert fc[0]["adjustment"] is None and fc[0]["final_revenue"] == fc[0]["expected_revenue"]


def test_gemini_down_keeps_the_baseline(gemini_down):
    fc, h = _forecasts()
    out = forecast.adjust(fc, _cond(sat={"weather": RAIN, "events": [], "transport": []}), h)
    assert out["error"] == "429" and out["adjusted"] == [] and SAT in out["kept"] and fc[0]["adjustment"] is None


def test_run_adjusts_from_stored_conditions_and_logs_it(db, gemini):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until=SAT), now="2026-12-01")
    conditions.record_weather([{"pitch_id": "broadway-market", "date": SAT, "source": "open-meteo",
                                "fetched_at": "2026-10-24T07:00:00Z", **RAIN}])
    gemini.answers = [[_day(SAT, -18, "-18% Sat: heavy rain all day", ("weather", f"weather:{SAT}", -18), confidence="high")]]
    out = forecast.run(now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc))
    stored = db[schema.FORECASTS].find_one({"date": SAT})
    assert stored["adjustment"]["reason"] == "-18% Sat: heavy rain all day" and stored["final_revenue"] == out[0]["final_revenue"]
    assert stored["final_revenue"] == round(stored["expected_revenue"] * 0.82, 2)
    log = db[schema.AGENT_LOG].find_one({"kind": "forecast_adjusted"})
    assert "heavy rain" in log["summary"] and db[schema.AGENT_LOG].count_documents({"kind": "gemini_fallback"}) == 0


def test_run_with_gemini_down_still_stores_the_baseline_and_logs_the_fallback(db, gemini_down):
    sales.record_orders(PosClient(now="2026-12-01").iter_sales(until=SAT), now="2026-12-01")
    conditions.record_weather([{"pitch_id": "broadway-market", "date": SAT, "source": "open-meteo",
                                "fetched_at": "2026-10-24T07:00:00Z", **RAIN}])
    forecast.run(now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc))
    stored = db[schema.FORECASTS].find_one({"date": SAT})
    assert stored["adjustment"] is None and stored["final_revenue"] == stored["expected_revenue"]
    log = db[schema.AGENT_LOG].find_one({"kind": "gemini_fallback"})
    assert "Gemini unavailable (429)" in log["summary"] and SAT in log["summary"]


# --- post-mortem regressions (Sat 24 Oct, ~23:30)

def test_a_rounded_percent_in_the_reason_is_accepted_and_tiny_changes_need_none():
    fc, _ = _forecasts()
    refs = forecast.day_conditions(SAT, {"weather": SUN, "events": [], "transport": []})
    assert forecast.check(fc[0], refs, _day(SAT, 12.5, "+13% Sat: sunny", ("weather", f"weather:{SAT}", 12.5))) == []
    assert forecast.check(fc[0], refs, _day(SAT, 0.3, "Sat: no real change", ("weather", f"weather:{SAT}", 0.3))) == []
    assert forecast.check(fc[0], refs, _day(SAT, 12, "+20% Sat: sunny", ("weather", f"weather:{SAT}", 12)))  # still wrong


def test_the_same_condition_cited_twice_is_rejected():
    fc, _ = _forecasts()
    refs = forecast.day_conditions(SAT, {"weather": RAIN, "events": [], "transport": []})
    twice = _day(SAT, -20, "-20% Sat: rain", ("weather", f"weather:{SAT}", -10), ("weather", f"weather:{SAT}", -10))
    assert any("more than once" in p for p in forecast.check(fc[0], refs, twice))
