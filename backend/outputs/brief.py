"""The owner's brief: what needs doing now, ready to send on WhatsApp."""
from datetime import datetime, timezone

from backend import approvals
from backend.db import schema
from backend.db.client import get_db

BUSINESS_ID = "gaia-coffee"
LEVELS = {0: "friendly nudge", 1: "firm reminder", 2: "final notice"}


def build_brief(dashboard_url: str = "") -> dict:
    db = get_db()
    now = datetime.now(timezone.utc)
    overdue = list(db[schema.INVOICES].find({"business_id": BUSINESS_ID, "status": "overdue"}))
    owed_to_us = [i for i in overdue if i["direction"] == "receivable"]
    we_owe = [i for i in overdue if i["direction"] == "payable"]
    review = list(db[schema.INVOICES].find({"business_id": BUSINESS_ID, "needs_review": True, "status": {"$ne": "paid"}}))
    todo = approvals.pending()

    lines = [f"☕ GAIA Coffee: {now:%a %d %b} (synthetic demo data)", ""]
    if todo:
        lines.append(f"Waiting for your approval ({len(todo)}):")
        for a in todo:
            tone = f" ({LEVELS[a['escalation_level']]})" if a.get("escalation_level") is not None else ""
            lines.append(f"#{a['ref']} {a['title']}{tone}")
        first = todo[0]["ref"]
        lines += ["", f'Reply "approve {first}" or "reject {first}".']
    else:
        lines.append("Nothing waiting for approval. ✅")
    lines.append("")
    if owed_to_us:
        lines.append(f"Customers owe you £{sum(i['amount'] for i in owed_to_us):,.2f} overdue across {len(owed_to_us)} invoice(s).")
    if we_owe:
        lines.append(f"You owe £{sum(i['amount'] for i in we_owe):,.2f} overdue to {len(we_owe)} supplier(s).")
    for inv in review:
        issue = (inv.get("verification_issues") or ["needs checking"])[0]
        lines.append(f"⚠️ Please check {inv['invoice_number']} ({inv['counterparty']}): {issue}")
    if dashboard_url:
        lines += ["", f"Dashboard: {dashboard_url}"]

    return {
        "business": "GAIA Coffee",
        "generated_at": now.isoformat(),
        "text": "\n".join(lines).strip(),
        "pending_approvals": [{"ref": a["ref"], "title": a["title"], "action_type": a["action_type"],
                               "draft": a["draft"], "reason": a["reason"]} for a in todo],
        "overdue_receivable_total": round(sum(i["amount"] for i in owed_to_us), 2),
        "overdue_payable_total": round(sum(i["amount"] for i in we_owe), 2),
        "needs_review": [i["invoice_number"] for i in review],
    }


KIND = {"invoice_reminder": "Reminder", "supplier_payment": "Payment"}
MORNING_ITEMS = 5  # approvals listed in the morning brief
DO_FIRST = 3


def _today_london():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/London")).date()


def morning_brief() -> str:
    """The owner's morning on WhatsApp, in CoFoundr's voice: today's forecast and weather (with a wind warning),
    the top things to do, and what's waiting for a yes. Built from MongoDB only: no Gemini, so it always arrives."""
    from bridge import view  # the dashboard's wind rule: one threshold for the page, the answers and the brief
    from backend import conditions

    db = get_db()
    today = _today_london()
    days = list(db[schema.FORECASTS].find({"business_id": BUSINESS_ID, "date": {"$gte": today.isoformat()}}).sort("date", 1).limit(8))
    lines = [f"Morning! ☕ Here's your *{today:%A} {today.day} {today:%b}*.", ""]

    f = days[0] if days and days[0]["date"] == today.isoformat() else None
    if f and f.get("trading"):
        rev = f.get("final_revenue", f.get("expected_revenue")) or 0
        lines.append(f"*Today:* about £{rev:,.0f} expected (£{f.get('low_revenue', 0):,.0f}–£{f.get('high_revenue', 0):,.0f})")
        why = (f.get("adjustment") or {}).get("reason")
        if why:
            lines.append(why)
    else:
        nxt = next((d for d in days if d.get("trading")), None)
        lines.append("*Today:* a day off, GAIA doesn't usually trade today." + (
            f" Next up: *{datetime.fromisoformat(nxt['date']):%A}*, about £{nxt.get('final_revenue', nxt.get('expected_revenue')) or 0:,.0f} expected."
            if nxt else ""))
    w = conditions.conditions_for([today.isoformat()])[today.isoformat()]["weather"]
    if w:
        lines.append(f"Weather: {(w.get('summary') or '').lower()}, up to {round(w.get('temp_max_c') or 0)}°"
                     + (f", {round(w['precip_chance_pct'])}% chance of rain" if w.get("precip_chance_pct") is not None else ""))
        if view.wind_warning(w):
            lines.append(f"💨 {view.wind_warning(w)}")

    tasks = [t for t in db[schema.TASKS].find({"business_id": BUSINESS_ID}).sort("rank", 1) if t.get("kind") != "approval"][:DO_FIRST]
    if tasks:
        lines += ["", "*Do first*"] + [f"{i}. {t['title']}" for i, t in enumerate(tasks, 1)]

    todo = approvals.pending()
    if todo:
        total = sum(a.get("amount") or 0 for a in todo)
        lines += ["", f"*Waiting for your yes* · {len(todo)} · £{total:,.2f}"]
        for a in todo[:MORNING_ITEMS]:
            lines.append(f"#{a['ref']} {a.get('counterparty') or a['title']} · {KIND.get(a.get('action_type'), 'Action')} · £{a['amount']:,.2f}")
        if len(todo) > MORNING_ITEMS:
            lines.append(f"+{len(todo) - MORNING_ITEMS} more")
        lines += ["", f"Want to send any of these now? Just reply *approve {todo[0]['ref']}* (or another number)."]
    else:
        lines += ["", "Nothing's waiting for your yes. Have a good one! ☀️"]
    return "\n".join(lines)
