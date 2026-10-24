"""Auto-prioritise (MVP2): what should the owner do first, and why.

1. Gather the open tasks from MongoDB (code, not Gemini): pending approvals, invoices waiting for
   the owner's check, and prep for the next trading day from the forecast.
2. Gemini ranks them by impact on the business with a one-line reason each.
3. Verify the ranking: tasks Gemini invents are dropped, tasks it leaves out are added back in rule order.
   If Gemini is unavailable, a rule ranking (money at stake × urgency) is used and logged.
4. Store the ranked list in `tasks` (each run replaces the last).

Usage (from the repo root):
    python -m backend.ai.prioritise            # rank and save
    python -m backend.ai.prioritise --dry-run  # print only
"""
import argparse
import json
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from google.genai import types
from pydantic import BaseModel

from backend.ai.gemini import MODEL, get_client, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
REVIEW_WEIGHT = 1.5  # an unchecked invoice blocks everything after it (can't chase or pay what isn't verified)


class Ranked(BaseModel):
    id: str
    impact: Literal["high", "medium", "low"]
    reason: str


class Ranking(BaseModel):
    tasks: List[Ranked]  # most important first


def candidates(db, now: datetime) -> List[dict]:
    """Every open task, with the facts to rank it by and a rule score (higher = sooner)."""
    tasks = []
    for a in db[schema.APPROVALS].find({"business_id": BUSINESS_ID, "status": "pending"}).sort("ref", 1):
        days = a.get("days_overdue", 0)
        pay = a["action_type"] == "supplier_payment"
        tasks.append({
            "_id": f"{BUSINESS_ID}:approval:{a['_id']}", "kind": "approval",
            "title": f"Approve #{a['ref']}: {a['title']}", "target_id": str(a["_id"]), "approval_ref": a["ref"],
            "amount": a["amount"],
            "facts": f"{'supplier bill GAIA owes' if pay else 'customer owes GAIA'}, £{a['amount']:.2f}, {days} days overdue"
                     + ("" if pay else f", reminder level {a.get('escalation_level') or 0} of 2") + "; waiting for the owner's yes",
            "score": a["amount"] * (1 + days / 7) * (1.2 if pay else 1.0),
        })
    for inv in db[schema.INVOICES].find({"business_id": BUSINESS_ID, "needs_review": True, "status": {"$ne": "paid"}}):
        issue = (inv.get("verification_issues") or ["needs checking"])[0]
        tasks.append({
            "_id": f"{BUSINESS_ID}:review_invoice:{inv['_id']}", "kind": "review_invoice",
            "title": f"Check invoice {inv['invoice_number']} from {inv['counterparty']}", "target_id": str(inv["_id"]),
            "approval_ref": None, "amount": inv["amount"],
            "facts": f"{inv['direction']}, £{inv['amount']:.2f}; automatic check failed: {issue}; can't be chased or paid until checked",
            "score": inv["amount"] * REVIEW_WEIGHT,
        })
    today = now.astimezone(LONDON).date().isoformat()
    day = db[schema.FORECASTS].find_one({"business_id": BUSINESS_ID, "trading": True, "date": {"$gte": today}}, sort=[("date", 1)])
    if day:
        when = "today" if day["date"] == today else day["weekday"]
        top = ", ".join(f"{p['name']} ×{p['expected_qty']:g}" for p in day["products"][:3])
        revenue = day.get("final_revenue", day["expected_revenue"])
        tasks.append({
            "_id": f"{BUSINESS_ID}:prep_day:{day['date']}", "kind": "prep_day",
            "title": f"Prep for {when} ({day['date']}): ~£{revenue:,.0f} expected", "target_id": day["_id"],
            "approval_ref": None, "amount": revenue,
            "facts": f"next trading day {day['weekday']} {day['date']}, £{day['low_revenue']:,.0f}–£{day['high_revenue']:,.0f} "
                     f"({day['basis']}); top sellers {top}",
            "score": revenue * (1.5 if when == "today" else 1.0),
        })
    return tasks


def rule_rank(tasks: List[dict]) -> List[dict]:
    out = sorted(tasks, key=lambda t: -t["score"])
    for t in out:
        t.update(impact="high" if t["score"] >= 300 else "medium" if t["score"] >= 100 else "low",
                 reason=f"Rule: {t['facts'].split(';')[0]}.", ranked_by="rule")
    return out


def gemini_rank(tasks: List[dict]) -> Dict[str, Ranked]:
    """Ask Gemini to order the tasks. Returns {task id: Ranked} in Gemini's order (may be partial or empty)."""
    listing = [{"id": t["_id"], "kind": t["kind"], "title": t["title"], "facts": t["facts"]} for t in tasks]
    prompt = (
        "You are the co-founder of GAIA Coffee, a one-person mobile coffee truck in East London. "
        "Rank the owner's open tasks below by impact on the business: cash flow, supplier relationships, "
        "customer relationships, and being ready to trade. Most important first. For each give an impact level "
        "and ONE short reason (under 20 words, British English) the owner will read on WhatsApp. "
        "Use only the ids given; don't add tasks.\n\n" + json.dumps(listing, indent=1)
    )
    response = with_retry(lambda: get_client().models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Ranking),
    ))
    return {r.id: r for r in Ranking.model_validate_json(response.text).tasks}


def rank(tasks: List[dict]) -> tuple:
    """Gemini's order, verified against the real tasks. Returns (ranked tasks, ids Gemini made up, ids it left out, error)."""
    by_rule = rule_rank([dict(t) for t in tasks])
    if not tasks:
        return [], [], [], None
    try:
        got = gemini_rank(tasks)
        error = None if got else "empty ranking"
    except Exception as e:  # quota after retries, bad JSON, network: rank by rule instead
        got, error = {}, getattr(e, "code", None) or type(e).__name__
    known = {t["_id"]: t for t in by_rule}
    invented = [i for i in got if i not in known]
    ranked = [{**known[i], "impact": r.impact, "reason": r.reason.strip() or known[i]["reason"], "ranked_by": "gemini"} for i, r in got.items() if i in known]
    missed = [t for t in by_rule if t["_id"] not in got]
    return ranked + missed, invented, [t["_id"] for t in missed] if got else [], error


def run(now: Optional[datetime] = None, dry_run: bool = False) -> List[dict]:
    from backend.db import activity
    from backend.db.client import get_db

    db = get_db()
    now = now or datetime.now(timezone.utc)
    ranked, invented, missed, error = rank(candidates(db, now))
    for i, t in enumerate(ranked, 1):
        t.pop("score")
        t.update(rank=i, business_id=BUSINESS_ID, ranked_at=now, synthetic=True)
    if not dry_run:
        db[schema.TASKS].delete_many({"business_id": BUSINESS_ID})
        if ranked:
            db[schema.TASKS].insert_many(ranked)
        if error or invented or missed:  # surfaced, never silent
            why = f"Gemini unavailable ({error})" if error else \
                  f"Gemini ranking corrected: {len(invented)} unknown task(s) dropped, {len(missed)} missing task(s) added by rule"
            activity.log("gemini_fallback", f"Priorities: {why}", invented=invented, missed=missed)
        by = "Gemini" if any(t["ranked_by"] == "gemini" for t in ranked) else "rule"
        activity.log("tasks_ranked", f"Ranked {len(ranked)} task(s) by {by}" +
                     (f"; first: {ranked[0]['title']}" if ranked else ""), task_ids=[t["_id"] for t in ranked])
    return ranked


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank the owner's open tasks with Gemini.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for t in run(dry_run=args.dry_run):
        print(f"{t['rank']}. [{t['impact']}] {t['title']}\n   why: {t['reason']}  ({t['ranked_by']})")


if __name__ == "__main__":
    main()
