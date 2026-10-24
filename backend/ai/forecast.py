"""Revenue forecast (MVP2): a baseline from till history, then Gemini adjusts it for weather, events and transport.

Baseline, per day ahead:
- GAIA only trades on some weekdays (Mon/Wed/Fri/Sat). Trading days are learned from the history:
  a weekday counts as trading if GAIA sold on it in at least half of the recent weeks. Other days are "closed".
- Expected revenue / orders = the average of the last LOOKBACK_WEEKS same weekdays it traded,
  with the range (lowest..highest of those days) so nobody mistakes a guess for a promise.
- Per product: average units sold on those same days → what to stock for that day.

Gemini adjustment, per trading day, with a verification loop:
1. Gemini gets the baseline, GAIA's daily takings so far, and that day's stored conditions (weather at the pitch,
   events nearby, TfL disruptions at our stations), each with a ref. It returns a % change, the factors behind it
   (each citing a ref and its own %) and one line for the owner.
2. Code checks every day: factors must cite conditions that really exist for that day, add up to the total, stay
   within limits, point the sensible way (rain or a closed station never raises takings), no conditions = no change,
   and the line the owner reads must state the same %.
3. Days that fail are sent back once with the exact problems. Still failing (or Gemini down): the baseline stands,
   logged as `gemini_fallback`. Days with no conditions stored are never sent to Gemini.

Usage (from the repo root):
    python -m backend.ai.forecast              # next 7 days, saved to `forecasts`
    python -m backend.ai.forecast --dry-run    # print only
    python -m backend.ai.forecast --no-gemini  # baseline only
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import BaseModel

from backend.ai.gemini import MODEL, get_client, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
LOOKBACK_WEEKS = 6
TOP_PRODUCTS = 8
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_PCT = 35  # biggest move for a whole day; more than that is a judgement call for the owner, not a model
MAX_FACTOR_PCT = 30
SUM_TOLERANCE = 0.5  # factors must add up to the day's % within this


class Factor(BaseModel):
    kind: Literal["weather", "event", "transport"]
    ref: str  # the ref given in the input, e.g. "weather:2026-10-24"
    effect_pct: float
    why: str


class DayAdjustment(BaseModel):
    date: str
    pct: float
    reason: str
    confidence: Literal["high", "medium", "low"]
    factors: List[Factor]


class Adjustments(BaseModel):
    days: List[DayAdjustment]


PROMPT = """You are the co-founder of GAIA Coffee, a one-person mobile coffee truck at Broadway Market, Hackney (East London).
For each trading day below, adjust the baseline takings for that day's conditions: the weather at the pitch, events
nearby and London transport disruptions at the stations near the pitch. Use GAIA's takings history as context.

Rules:
- Only use the conditions listed for that day. Cite each one you use by its exact `ref`; never invent conditions.
- Each factor's effect_pct is its own % change; the factors must add up exactly to the day's pct.
- A day with no conditions, or conditions that don't matter, gets pct 0 and no factors.
- Rain, drizzle, showers or snow never raise takings. A transport disruption never raises takings.
- Keep it realistic: the whole day within ±{max_pct}%, each factor within ±{max_factor}%.
- `reason`: ONE line for the owner on WhatsApp, under 15 words, British English, starting with the signed % and the day,
  e.g. "+12% Sat: dry and mild, market crowds" or "-18% Mon: heavy rain all day". Say "no change" when pct is 0.
- `confidence`: how sure you are, given how far ahead the day is and how clear the conditions are.

Trading days to adjust:
{days}

GAIA's daily takings so far (synthetic demo data):
{history}"""

RECHECK = """Automatic checks rejected your adjustment for some days:
{problems}

Your previous answer for those days:
{previous}

Answer again for those days only, fixing every problem. Follow the rules above exactly."""


def daily_history(orders: List[dict]) -> Dict[date, dict]:
    """Orders → {London date: {"revenue", "orders"}}."""
    days: Dict[date, dict] = defaultdict(lambda: {"revenue": 0.0, "orders": 0})
    for o in orders:
        d = o["placed_at"].astimezone(LONDON).date()
        days[d]["revenue"] += o["total"]
        days[d]["orders"] += 1
    return dict(days)


def daily_product_qty(sales: List[dict]) -> Dict[date, Dict[str, float]]:
    """Sale lines → {London date: {sku: units}}."""
    days: Dict[date, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in sales:
        days[s["ts"].astimezone(LONDON).date()][s["meta"]["sku"]] += s["qty"]
    return {d: dict(q) for d, q in days.items()}


def trading_weekdays(history: Dict[date, dict], today: date) -> set:
    """Weekdays (0=Mon) GAIA traded on in at least half of the recent weeks (up to LOOKBACK_WEEKS).
    "Recent weeks" is capped by how much history exists, so a business with 2 weeks of data still gets a forecast."""
    since = today - timedelta(weeks=LOOKBACK_WEEKS)
    recent = [d for d in history if since <= d < today]
    if not recent:
        return set()
    weeks = min(LOOKBACK_WEEKS, -(-((today - min(recent)).days) // 7))  # whole weeks of history, rounded up
    seen = defaultdict(int)
    for d in recent:
        seen[d.weekday()] += 1
    return {wd for wd, n in seen.items() if n >= weeks / 2}


def baseline(history: Dict[date, dict], product_days: Dict[date, Dict[str, float]], names: Dict[str, str],
             today: date, days: int = 7) -> List[dict]:
    """Pure function: the baseline forecast for `days` days starting at `today` (London dates)."""
    open_days = trading_weekdays(history, today)
    out = []
    for i in range(days):
        target = today + timedelta(days=i)
        wd = target.weekday()
        past = sorted((d for d in history if d.weekday() == wd and d < today), reverse=True)[:LOOKBACK_WEEKS]
        doc = {"_id": f"{BUSINESS_ID}:{target.isoformat()}", "business_id": BUSINESS_ID, "date": target.isoformat(),
               "weekday": DAYS[wd], "trading": wd in open_days and bool(past), "adjustment": None, "synthetic": True}
        if not doc["trading"]:
            doc.update(expected_revenue=0.0, expected_orders=0.0, low_revenue=0.0, high_revenue=0.0, products=[],
                       basis=f"closed: GAIA doesn't usually trade on {DAYS[wd]}s" if past or wd not in open_days
                       else f"no {DAYS[wd]} history yet")
        else:
            revenue = [history[d]["revenue"] for d in past]
            qty = defaultdict(list)
            for d in past:
                for sku, n in product_days.get(d, {}).items():
                    qty[sku].append(n)
            expected = {sku: sum(v) / len(past) for sku, v in qty.items()}  # days a product didn't sell count as 0
            top = sorted(expected.items(), key=lambda kv: -kv[1])[:TOP_PRODUCTS]
            doc.update(expected_revenue=round(mean(revenue), 2),
                       expected_orders=round(mean(history[d]["orders"] for d in past), 1),
                       low_revenue=round(min(revenue), 2), high_revenue=round(max(revenue), 2),
                       basis=f"average of the last {len(past)} {DAYS[wd]}{'s' if len(past) != 1 else ''}",
                       products=[{"sku": sku, "name": names.get(sku, sku), "expected_qty": round(q, 1)} for sku, q in top])
        doc["final_revenue"] = doc["expected_revenue"]
        out.append(doc)
    return out


# ---------------------------------------------------------------- Gemini adjustment

def is_wet(w: dict) -> bool:
    return (w.get("weather_code") or 0) >= 51 or (w.get("precip_mm") or 0) >= 2 or (w.get("precip_chance_pct") or 0) >= 60


def day_conditions(date_: str, cond: dict) -> Dict[str, dict]:
    """{ref: {"kind", "label", "wet"/"facts"...}} for one day: what Gemini may cite."""
    out = {}
    w = cond.get("weather")
    if w and w.get("weather_code") is not None:
        out[f"weather:{date_}"] = {
            "kind": "weather", "label": w["summary"], "wet": is_wet(w),
            "facts": {k: w.get(k) for k in ("summary", "temp_max_c", "temp_min_c", "precip_mm", "precip_chance_pct", "wind_max_kmh")}}
    for e in cond.get("events", []):
        out[e["_id"]] = {"kind": "event", "label": e["name"],
                         "facts": {"name": e["name"], "category": e.get("category"), "venue": e.get("venue"),
                                   "distance_km": e.get("distance_km"),
                                   "starts": e["starts_at"].astimezone(LONDON).strftime("%H:%M") if e.get("starts_at") else None}}
    for t in cond.get("transport", []):
        out[t["_id"]] = {"kind": "transport", "label": f"{t['line_name']} {t['status'].lower()} at {', '.join(t['stations'])}",
                         "facts": {"line": t["line_name"], "status": t["status"], "impact": t["impact"], "stations": t["stations"],
                                   "nearest_m": t["nearest_m"], "tfl_says": t["reason"][:300]}}
    return out


def check(day: dict, refs: Dict[str, dict], adj: DayAdjustment) -> List[str]:
    """Deterministic checks on one day's adjustment. Empty list = accepted."""
    problems = []
    for f in adj.factors:
        known = refs.get(f.ref)
        if known is None:
            problems.append(f"factor {f.ref!r} is not one of the conditions given for {day['date']}")
            continue
        if known["kind"] != f.kind:
            problems.append(f"factor {f.ref!r} is {known['kind']}, not {f.kind}")
        if abs(f.effect_pct) > MAX_FACTOR_PCT:
            problems.append(f"factor {f.ref!r} is {f.effect_pct:+g}%, beyond ±{MAX_FACTOR_PCT}%")
        if f.effect_pct > 0 and (known["kind"] == "transport" or known.get("wet")):
            problems.append(f"factor {f.ref!r} ({known['label']}) can't raise takings, but has {f.effect_pct:+g}%")
    total = sum(f.effect_pct for f in adj.factors)
    if abs(total - adj.pct) > SUM_TOLERANCE:
        problems.append(f"factors add up to {total:+g}%, but the day's pct is {adj.pct:+g}%")
    if abs(adj.pct) > MAX_PCT:
        problems.append(f"pct {adj.pct:+g}% is beyond ±{MAX_PCT}%")
    if adj.pct and str(round(abs(adj.pct))) not in {str(round(float(n))) for n in re.findall(r"(\d+(?:\.\d+)?)\s*%", adj.reason)}:
        problems.append(f"reason {adj.reason!r} doesn't state the day's {adj.pct:+.0f}%")
    if not adj.reason.strip():
        problems.append("reason is empty")
    return problems


def ask_gemini(prompt: str) -> List[DayAdjustment]:
    response = with_retry(lambda: get_client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Adjustments),
    ))
    if not response.text:
        raise ValueError("Gemini returned an empty response.")
    return Adjustments.model_validate_json(response.text).days


def adjust(forecasts: List[dict], conditions: Dict[str, dict], history: Dict[date, dict]) -> dict:
    """Adjust trading days in place (adjustment + final_revenue). Returns a summary for the log:
    {"adjusted": [dates], "kept": {date: problems}, "skipped": [dates without conditions], "attempts", "error"}."""
    summary = {"adjusted": [], "kept": {}, "skipped": [], "attempts": 0, "error": None}
    days = {}
    for f in forecasts:
        if not f["trading"]:
            continue
        refs = day_conditions(f["date"], conditions.get(f["date"]) or {})
        if refs:
            days[f["date"]] = (f, refs)
        else:
            summary["skipped"].append(f["date"])
    if not days:
        return summary
    listing = [{"date": d, "weekday": f["weekday"], "baseline_gbp": f["expected_revenue"],
                "range_gbp": [f["low_revenue"], f["high_revenue"]], "basis": f["basis"],
                "conditions": [{"ref": r, "kind": c["kind"], **c["facts"]} for r, c in refs.items()]} for d, (f, refs) in days.items()]
    takings = [{"date": d.isoformat(), "weekday": DAYS[d.weekday()][:3], "gbp": round(v["revenue"], 2)} for d, v in sorted(history.items())]
    prompt = PROMPT.format(max_pct=MAX_PCT, max_factor=MAX_FACTOR_PCT, days=json.dumps(listing, indent=1, default=str),
                           history=json.dumps(takings, separators=(",", ":")))
    accepted: Dict[str, DayAdjustment] = {}
    answers: Dict[str, DayAdjustment] = {}
    problems: Dict[str, List[str]] = {d: ["no answer for this day"] for d in days}
    try:
        for attempt in (1, 2):
            if attempt == 2:
                prompt += "\n\n" + RECHECK.format(
                    problems="\n".join(f"- {d}: {'; '.join(p)}" for d, p in problems.items()),
                    previous=json.dumps([answers[d].model_dump() for d in problems if d in answers], indent=1))
            summary["attempts"] = attempt
            for a in ask_gemini(prompt):
                if a.date in problems:  # days already accepted, or not asked about, are ignored
                    answers[a.date] = a
            for d in list(problems):
                if d in answers:
                    found = check(days[d][0], days[d][1], answers[d])
                    if not found:
                        accepted[d] = answers[d]
                        del problems[d]
                    else:
                        problems[d] = found
            if not problems:
                break
    except Exception as e:  # quota after retries, bad JSON, network: what was accepted stands, the rest keeps its baseline
        summary["error"] = str(getattr(e, "code", None) or type(e).__name__)
    for d, a in accepted.items():
        f, refs = days[d]
        f["adjustment"] = {
            "pct": round(a.pct, 1), "reason": a.reason.strip(), "confidence": a.confidence, "by": "gemini",
            "sources": sorted({x.kind for x in a.factors}), "attempts": summary["attempts"],
            "factors": [{"kind": x.kind, "ref": x.ref, "label": refs[x.ref]["label"], "effect_pct": round(x.effect_pct, 1),
                         "why": x.why.strip()} for x in a.factors]}
        f["final_revenue"] = round(f["expected_revenue"] * (1 + a.pct / 100), 2)
        summary["adjusted"].append(d)
    summary["kept"] = problems
    return summary


def run(days: int = 7, now: Optional[datetime] = None, dry_run: bool = False, use_gemini: bool = True) -> List[dict]:
    """Recompute the next `days` days from MongoDB history, let Gemini adjust them, and store them (latest run wins)."""
    from backend import conditions
    from backend.db import activity
    from backend.db.client import get_db

    db = get_db()
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(LONDON).date()
    since = datetime.combine(today - timedelta(weeks=LOOKBACK_WEEKS + 1), datetime.min.time(), LONDON)
    until = datetime.combine(today, datetime.min.time(), LONDON)  # only complete days: today isn't over yet
    window = {"$gte": since, "$lt": until}
    orders = list(db[schema.ORDERS].find({"business_id": BUSINESS_ID, "placed_at": window}, {"placed_at": 1, "total": 1}))
    sales = list(db[schema.SALES].find({"meta.business_id": BUSINESS_ID, "ts": window}, {"ts": 1, "meta.sku": 1, "qty": 1}))
    names = {p["_id"]: p["name"] for p in db[schema.PRODUCTS].find({"business_id": BUSINESS_ID}, {"name": 1})}

    history = daily_history(orders)
    forecasts = baseline(history, daily_product_qty(sales), names, today, days)
    summary = adjust(forecasts, conditions.conditions_for([f["date"] for f in forecasts]), history) if use_gemini else None
    if not dry_run:
        for f in forecasts:
            f["generated_at"] = now
            db[schema.FORECASTS].replace_one({"_id": f["_id"]}, f, upsert=True)
        trading = [f for f in forecasts if f["trading"]]
        activity.log("forecast_updated", f"Forecast for {forecasts[0]['date']}–{forecasts[-1]['date']}: "
                     f"{len(trading)} trading day(s), £{sum(f['expected_revenue'] for f in trading):,.0f} expected "
                     f"(baseline from {len(orders)} orders)", days=[f["date"] for f in forecasts])
        if summary:
            log_adjustment(forecasts, summary)
    return forecasts


def log_adjustment(forecasts: List[dict], summary: dict) -> None:
    """One line for what Gemini changed; failures and rejected answers surfaced, never silent."""
    from backend.db import activity

    by_date = {f["date"]: f for f in forecasts}
    if summary["adjusted"]:
        lines = "; ".join(by_date[d]["adjustment"]["reason"] for d in summary["adjusted"])
        activity.log("forecast_adjusted", f"Gemini adjusted {len(summary['adjusted'])} day(s), checked"
                     f"{' after a recheck' if summary['attempts'] > 1 else ''}: {lines}",
                     days=summary["adjusted"], attempts=summary["attempts"])
    if summary["error"] or summary["kept"]:
        why = f"Gemini unavailable ({summary['error']})" if summary["error"] else "Gemini's adjustment failed the checks twice"
        activity.log("gemini_fallback", f"Forecast: {why}; baseline kept for {', '.join(sorted(summary['kept']))}",
                     problems=summary["kept"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline revenue forecast from till history.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gemini", action="store_true", help="baseline only")
    args = parser.parse_args()
    for f in run(days=args.days, dry_run=args.dry_run, use_gemini=not args.no_gemini):
        if not f["trading"]:
            print(f"{f['date']} {f['weekday']:<9}  closed  ({f['basis']})")
            continue
        top = ", ".join(f"{p['name']} ×{p['expected_qty']:g}" for p in f["products"][:3])
        print(f"{f['date']} {f['weekday']:<9}  £{f['expected_revenue']:>7,.2f}  (£{f['low_revenue']:,.0f}–£{f['high_revenue']:,.0f}), "
              f"~{f['expected_orders']:g} orders | {top} | {f['basis']}")
        a = f["adjustment"]
        if a:
            print(f"{'':<21}→ £{f['final_revenue']:>7,.2f}  {a['reason']}  [{a['confidence']}]")
            for x in a["factors"]:
                print(f"{'':<23}{x['effect_pct']:+g}% {x['kind']}: {x['label']} ({x['why']})")


if __name__ == "__main__":
    main()
