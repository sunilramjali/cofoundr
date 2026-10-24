"""Revenue forecast: baseline from till history (MVP2). Gemini's weather/events adjustment comes on top later.

Baseline, per day ahead:
- GAIA only trades on some weekdays (Mon/Wed/Fri/Sat). Trading days are learned from the history:
  a weekday counts as trading if GAIA sold on it in at least half of the recent weeks. Other days are "closed".
- Expected revenue / orders = the average of the last LOOKBACK_WEEKS same weekdays it traded,
  with the range (lowest..highest of those days) so nobody mistakes a guess for a promise.
- Per product: average units sold on those same days → what to stock for that day.

Usage (from the repo root):
    python -m backend.ai.forecast              # next 7 days, saved to `forecasts`
    python -m backend.ai.forecast --dry-run    # print only
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.db import schema

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
LOOKBACK_WEEKS = 6
TOP_PRODUCTS = 8
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


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


def run(days: int = 7, now: Optional[datetime] = None, dry_run: bool = False) -> List[dict]:
    """Recompute the next `days` days from MongoDB history and store them (latest run wins)."""
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

    forecasts = baseline(daily_history(orders), daily_product_qty(sales), names, today, days)
    if not dry_run:
        for f in forecasts:
            f["generated_at"] = now
            db[schema.FORECASTS].replace_one({"_id": f["_id"]}, f, upsert=True)
        trading = [f for f in forecasts if f["trading"]]
        activity.log("forecast_updated", f"Forecast for {forecasts[0]['date']}–{forecasts[-1]['date']}: "
                     f"{len(trading)} trading day(s), £{sum(f['expected_revenue'] for f in trading):,.0f} expected "
                     f"(baseline from {len(orders)} orders)", days=[f["date"] for f in forecasts])
    return forecasts


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline revenue forecast from till history.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for f in run(days=args.days, dry_run=args.dry_run):
        if not f["trading"]:
            print(f"{f['date']} {f['weekday']:<9}  closed  ({f['basis']})")
            continue
        top = ", ".join(f"{p['name']} ×{p['expected_qty']:g}" for p in f["products"][:3])
        print(f"{f['date']} {f['weekday']:<9}  £{f['expected_revenue']:>7,.2f}  (£{f['low_revenue']:,.0f}–£{f['high_revenue']:,.0f}), "
              f"~{f['expected_orders']:g} orders | {top} | {f['basis']}")


if __name__ == "__main__":
    main()
