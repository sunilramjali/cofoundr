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
        lines += ["", 'Reply "approve 1" or "reject 1".']
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
