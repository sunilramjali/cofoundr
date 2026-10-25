"""The forecast marks its own homework (MVP2): grade past forecasts against actual takings, learn, earn trust.

1. Grade (code): for each past trading day with a stored forecast and actual takings, the error of the plain
   baseline and of Gemini's adjusted forecast. Whether Gemini's adjustment *helped* is measured, never self-reported.
2. Lessons (Gemini, checked): Gemini explains the misses and writes up to 3 lessons, each about one kind of factor
   (weather, event, transport, or overall) with a direction (adjust less / keep / adjust more) and the graded days it
   rests on. Code rejects a lesson whose days don't exist or whose direction contradicts the measured results.
3. Earned trust: the next forecast's adjustment cap shrinks when adjustments have mostly made forecasts worse and
   grows back as they help (EARNED_CAPS), and the accepted lessons go into its prompt.

Backtest (so a new business has a track record on day one): replay recent trading days as if forecasting each one
the day before, with the weather that was actually observed (rain during trading hours), and grade them the same way.
Only history before the first replayed day is shown to Gemini, so it can't peek at the answers.

Usage (from the repo root):
    python -m backend.ai.review               # grade past forecasts, write lessons
    python -m backend.ai.review --backtest 21 # replay the last 21 days (Gemini + Open-Meteo archive), then grade
"""
import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Dict, List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import BaseModel

from backend.ai import forecast
from backend.ai.gemini import HIGH, MODEL, get_client, thinking, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
RECORD_DAYS = 10  # the trust cap looks at the last 10 graded, adjusted days
MIN_RECORD = 4  # fewer graded adjustments than this: not enough to judge, full cap
EARNED_CAPS = ((0.6, 35.0), (0.4, 20.0), (0.0, 10.0))  # share of adjustments that helped → max ±% per day
MAX_LESSONS = 3
MAX_LESSON_WORDS = 45
KINDS = ("weather", "event", "transport")


class Lesson(BaseModel):
    text: str  # one sentence the next forecast will follow
    about: Literal["weather", "event", "transport", "overall"]
    direction: Literal["adjust_less", "keep", "adjust_more"]
    evidence_dates: List[str]


class Lessons(BaseModel):
    lessons: List[Lesson]


PROMPT = """You are GAIA Coffee's forecaster, reviewing your own past forecasts against what the truck actually took.
For each day: the plain baseline (average of recent same weekdays), your adjusted forecast with the factors you used,
and the actual takings. The errors are measured by code.

Graded days:
{days}

Measured, per kind of factor (mean absolute error of the baseline vs of your adjusted forecast, on days you used it):
{stats}

Write at most {n} lessons for your next forecasts. Each lesson: one sentence (under {words} words, British English),
`about` one kind of factor (or overall), a `direction` (adjust_less if your adjustments made things worse, keep if
they were about right, adjust_more if they were too timid) that matches the measured numbers, and the dates it
rests on. Don't write a lesson the numbers don't support."""


# ---------------------------------------------------------------- grading (code only)

def grade(forecast_doc: dict, actual: float, mode: str, now: datetime) -> dict:
    expected, final = forecast_doc["expected_revenue"], forecast_doc.get("final_revenue", forecast_doc["expected_revenue"])
    adj = forecast_doc.get("adjustment") or None
    base_err = abs(actual - expected) / actual * 100 if actual else None
    final_err = abs(actual - final) / actual * 100 if actual else None
    adjusted = bool(adj and adj.get("pct"))
    return {
        "_id": f"{BUSINESS_ID}:{mode}:{forecast_doc['date']}", "business_id": BUSINESS_ID, "date": forecast_doc["date"],
        "weekday": forecast_doc["weekday"], "mode": mode, "expected": expected, "final": final, "actual": round(actual, 2),
        "baseline_error_pct": round(base_err, 1) if base_err is not None else None,
        "final_error_pct": round(final_err, 1) if final_err is not None else None,
        "adjustment_pct": adj.get("pct") if adj else None,
        "factors": [{"kind": f["kind"], "label": f["label"], "effect_pct": f["effect_pct"]} for f in (adj or {}).get("factors", [])],
        # helped = the adjustment moved the forecast closer to what happened (None: no adjustment to judge)
        "helped": (final_err < base_err) if adjusted and base_err is not None else None,
        "graded_at": now, "synthetic": forecast_doc.get("synthetic", True),
    }


def stats(reviews: List[dict]) -> Dict[str, dict]:
    """Per factor kind (and overall): days, mean |error| of baseline vs adjusted, and how many adjustments helped."""
    out = {}
    for kind in (*KINDS, "overall"):
        days = [r for r in reviews if r["helped"] is not None and (kind == "overall" or any(f["kind"] == kind for f in r["factors"]))]
        if days:
            out[kind] = {"days": len(days), "baseline_error": round(mean(r["baseline_error_pct"] for r in days), 1),
                         "adjusted_error": round(mean(r["final_error_pct"] for r in days), 1),
                         "helped": sum(1 for r in days if r["helped"]), "dates": [r["date"] for r in days]}
    return out


def check_lesson(lesson: Lesson, measured: Dict[str, dict]) -> List[str]:
    """Empty = accepted. The direction must agree with the measured errors; the dates must be graded days."""
    problems = []
    s = measured.get(lesson.about)
    if not s or s["days"] < 2:
        return [f"not enough graded days with a {lesson.about} adjustment to learn from"]
    unknown = [d for d in lesson.evidence_dates if d not in s["dates"]]
    if unknown or not lesson.evidence_dates:
        problems.append(f"evidence dates {unknown or '[]'} aren't graded days with a {lesson.about} adjustment")
    diff = s["adjusted_error"] - s["baseline_error"]  # > 0: adjusting made it worse
    allowed = ("adjust_less",) if diff > 1 else ("keep", "adjust_more") if diff < -1 else ("keep", "adjust_less")
    if lesson.direction not in allowed:
        problems.append(f"direction {lesson.direction} contradicts the numbers (adjusted error {s['adjusted_error']}% vs "
                        f"baseline {s['baseline_error']}%)")
    if len(lesson.text.split()) > MAX_LESSON_WORDS or not lesson.text.strip():
        problems.append("lesson must be one short sentence")
    return problems


# ---------------------------------------------------------------- trust + lessons, read by forecast.run

def earned_cap(db) -> Tuple[float, str]:
    """(max ±% Gemini may adjust a day by, why). Shrinks when its recent adjustments mostly made forecasts worse."""
    recent, seen = [], set()
    for r in db[schema.FORECAST_REVIEWS].find({"business_id": BUSINESS_ID, "helped": {"$ne": None}}).sort([("date", -1), ("mode", -1)]):
        if r["date"] not in seen:  # a day graded live and in a backtest counts once (live sorts first)
            seen.add(r["date"])
            recent.append(r)
        if len(recent) == RECORD_DAYS:
            break
    if len(recent) < MIN_RECORD:
        return forecast.MAX_PCT, f"{len(recent)} graded adjustment(s): not enough to judge yet"
    helped = sum(1 for r in recent if r["helped"])
    ratio = helped / len(recent)
    cap = next(c for floor, c in EARNED_CAPS if ratio >= floor)
    return min(cap, forecast.MAX_PCT), f"adjustments helped on {helped} of the last {len(recent)} graded days"


def current_lessons(db) -> List[str]:
    return [lesson["text"] for lesson in db[schema.LESSONS].find({"business_id": BUSINESS_ID, "active": True}).sort("created_at", -1).limit(MAX_LESSONS)]


# ---------------------------------------------------------------- run

def ask_gemini(prompt: str) -> List[Lesson]:
    response = with_retry(lambda: get_client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(thinking_config=thinking(HIGH), response_mime_type="application/json", response_schema=Lessons),
    ))
    if not response.text:
        raise ValueError("Gemini returned an empty response.")
    return Lessons.model_validate_json(response.text).lessons


def learn(db, reviews: List[dict], now: datetime, store: bool = True) -> Tuple[List[dict], List[str]]:
    """Gemini's lessons from graded days, checked. Accepted ones replace the active lessons. Returns (accepted, rejected)."""
    measured = stats(reviews)
    if not measured:
        return [], []
    days = [{k: r[k] for k in ("date", "weekday", "expected", "final", "actual", "baseline_error_pct", "final_error_pct", "factors")}
            for r in reviews]
    prompt = PROMPT.format(days=json.dumps(days, indent=1), stats=json.dumps(measured, indent=1), n=MAX_LESSONS, words=MAX_LESSON_WORDS)
    accepted, rejected = [], []
    for lesson in ask_gemini(prompt)[:MAX_LESSONS]:
        problems = check_lesson(lesson, measured)
        if problems:
            rejected.append(f"{lesson.text[:80]}: {'; '.join(problems)}")
            continue
        accepted.append({"business_id": BUSINESS_ID, **lesson.model_dump(), "measured": measured[lesson.about],
                         "active": True, "created_at": now, "synthetic": reviews[0]["synthetic"]})
    if accepted and store:
        db[schema.LESSONS].update_many({"business_id": BUSINESS_ID, "active": True}, {"$set": {"active": False}})
        db[schema.LESSONS].insert_many(accepted)
    return accepted, rejected


def actuals(db, today: date) -> Tuple[bool, Dict[date, dict]]:
    since = datetime.combine(today - timedelta(weeks=forecast.LOOKBACK_WEEKS + 8), time.min, LONDON)
    real, history, _, _, _ = forecast.load(db, since, datetime.combine(today, time.min, LONDON))
    return real, history


def grade_stored(db, now: datetime) -> List[dict]:
    """Grade every stored forecast for a finished trading day that has actual takings (idempotent: re-grading replaces)."""
    today = now.astimezone(LONDON).date()
    _, history = actuals(db, today)
    out = []
    for f in db[schema.FORECASTS].find({"business_id": BUSINESS_ID, "trading": True, "date": {"$lt": today.isoformat()}}):
        day = history.get(date.fromisoformat(f["date"]))
        if day and day["revenue"] > 0:
            out.append(grade(f, day["revenue"], "live", now))
    return out


def backtest(db, days: int, now: datetime) -> Tuple[List[dict], dict]:
    """Replay the last `days` days' trading days as forecasts made the day before, with observed weather. Returns
    (graded reviews, the adjustment summary). Gemini only sees takings from before the first replayed day."""
    from backend.inputs import fetch, weather
    today = now.astimezone(LONDON).date()
    real, history = actuals(db, today)
    targets = sorted(d for d in history if today - timedelta(days=days) <= d < today and history[d]["revenue"] > 0)
    if not targets:
        return [], {"adjusted": [], "kept": {}, "skipped": [], "attempts": 0, "error": None}
    forecasts = []
    for d in targets:
        f = forecast.baseline(history, {}, {}, d, days=1)[0]  # baseline only uses days before d
        if f["trading"]:
            f["synthetic"] = not real
            forecasts.append(f)
    try:
        observed = {w["date"]: w for w in weather.history(targets[0], targets[-1])}
    except (fetch.FetchError, ValueError):  # archive down: grade the plain baselines, nothing for Gemini to adjust
        observed = {}
    conditions = {f["date"]: {"weather": observed.get(f["date"]), "events": [], "transport": []} for f in forecasts}
    before = {d: v for d, v in history.items() if d < targets[0]}  # no peeking at the answers
    summary = forecast.adjust(forecasts, conditions, before, synthetic=not real)
    return [grade(f, history[date.fromisoformat(f["date"])]["revenue"], "backtest", now) for f in forecasts], summary


def run(now: Optional[datetime] = None, backtest_days: Optional[int] = None, dry_run: bool = False) -> dict:
    from backend.db import activity
    from backend.db.client import get_db

    db = get_db()
    now = now or datetime.now(timezone.utc)
    summary = None
    if backtest_days:
        reviews, summary = backtest(db, backtest_days, now)
    else:
        reviews = grade_stored(db, now)
    accepted, rejected, error = [], [], None
    if reviews:
        if not dry_run:
            for r in reviews:
                db[schema.FORECAST_REVIEWS].replace_one({"_id": r["_id"]}, r, upsert=True)
        try:
            accepted, rejected = learn(db, reviews, now, store=not dry_run)
        except Exception as e:  # lessons are a bonus: grading still stands
            error = str(getattr(e, "code", None) or type(e).__name__)
    measured = stats(reviews).get("overall")
    result = {"graded": len(reviews), "overall": measured, "lessons": [a["text"] for a in accepted], "rejected": rejected,
              "error": error, "adjust_summary": summary, "reviews": reviews}
    if not dry_run and reviews:
        line = (f"Graded {len(reviews)} {'replayed ' if backtest_days else ''}forecast(s) against actual takings"
                + (f": adjustments helped on {measured['helped']} of {measured['days']} days (error {measured['baseline_error']}% "
                   f"baseline → {measured['adjusted_error']}% adjusted)" if measured else "")
                + (f"; {len(accepted)} lesson(s) learned" if accepted else "")
                + (f"; {len(rejected)} lesson(s) rejected by the check" if rejected else ""))
        activity.log("forecast_graded", line, lessons=result["lessons"], rejected=rejected)
        if error:
            activity.log("gemini_fallback", f"Forecast lessons: Gemini unavailable ({error}); grades kept, no new lessons")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade forecasts against actual takings and learn lessons.")
    parser.add_argument("--backtest", type=int, metavar="DAYS", help="replay the last DAYS days first (Gemini + observed weather)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run(backtest_days=args.backtest, dry_run=args.dry_run)
    for r in result["reviews"]:
        adj = f"{r['adjustment_pct']:+g}%" if r["adjustment_pct"] is not None else "none"
        print(f"{r['date']} {r['weekday'][:3]}  baseline £{r['expected']:.0f} ({r['baseline_error_pct']}% off)  "
              f"adjusted {adj} → £{r['final']:.0f} ({r['final_error_pct']}% off)  actual £{r['actual']:.0f}  "
              f"{'helped' if r['helped'] else 'hurt' if r['helped'] is False else ''}  {', '.join(f['label'] for f in r['factors'])}")
    print("\noverall:", result["overall"])
    for t in result["lessons"]:
        print("LESSON:", t)
    for t in result["rejected"]:
        print("REJECTED:", t)
    if result["error"]:
        print("lessons unavailable:", result["error"])


if __name__ == "__main__":
    main()
