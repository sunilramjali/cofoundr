"""WhatsApp automatic updates: what the owner should hear about, unprompted (MVP1).

Built on the agent activity log. Saint polls `GET /updates` and sends each message
to the owner on WhatsApp. Two ways to track what's already been delivered:
- stateless: Saint passes `since=<cursor>` from the previous response's `next_since`
- server-side (default): no `since` → CoFoundr remembers the cursor per consumer and
  advances it on each call, so Saint only needs to call the URL on a schedule.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from backend.db import schema
from backend.db.client import get_db

BUSINESS_ID = "gaia-coffee"
RELEVANT = ["invoice_extracted", "overdue_check", "approval_decided"]
FIRST_CALL_WINDOW = timedelta(hours=2)  # no cursor yet → only recent events, never the whole history
MAX_EVENTS = 20


def invoice_message(inv: dict) -> str:
    if inv.get("needs_review"):
        issue = (inv.get("verification_issues") or ["needs checking"])[0]
        return f"⚠️ New invoice {inv['invoice_number']} from {inv['counterparty']} (£{inv['amount']:.2f}) needs your check: {issue}"
    due = f", due {inv['due_date']:%d %b}" if inv.get("due_date") else ""
    return f"📥 New invoice {inv['invoice_number']} from {inv['counterparty']}: £{inv['amount']:.2f}{due}. Read and checked ✓"


def approval_message(a: dict) -> str:
    return f"📝 #{a['ref']} {a['title']}. Reply \"approve {a['ref']}\" or \"reject {a['ref']}\"."


def messages_for(event: dict, db) -> List[dict]:
    """Turn one activity-log event into zero or more owner messages."""
    kind, detail = event["kind"], event.get("detail") or {}
    if kind == "invoice_extracted":
        # By _id: invoice numbers alone aren't unique across suppliers
        query = {"_id": ObjectId(detail["invoice_id"])} if detail.get("invoice_id") else \
            {"business_id": BUSINESS_ID, "invoice_number": detail.get("invoice_number")}
        inv = db[schema.INVOICES].find_one(query)
        return [{"kind": kind, "needs_reply": False, "message": invoice_message(inv)}] if inv else []
    if kind == "overdue_check":
        refs = detail.get("refs") or []
        pending = db[schema.APPROVALS].find({"business_id": BUSINESS_ID, "ref": {"$in": refs}, "status": "pending"}).sort("ref", 1)
        return [{"kind": "approval_needed", "ref": a["ref"], "needs_reply": True, "message": approval_message(a)} for a in pending]
    if kind == "approval_decided" and event.get("actor") != "saint":
        # Decisions made on the dashboard; the owner already got a reply for WhatsApp ones.
        emoji = "❌" if detail.get("decision") == "reject" else "✅"
        return [{"kind": kind, "needs_reply": False, "message": f"{emoji} {event['summary']}"}]
    return []


def _cursor_query(since: Optional[str]) -> dict:
    if since:
        try:
            return {"_id": {"$gt": ObjectId(since)}}
        except InvalidId:
            raise ValueError("`since` must be the `next_since` value from a previous /updates response.")
    return {"at": {"$gte": datetime.now(timezone.utc) - FIRST_CALL_WINDOW}}


def get_updates(since: Optional[str] = None, consumer: str = "saint", peek: bool = False) -> dict:
    db = get_db()
    cursors = db[schema.CURSORS]
    stored = None
    if since is None:
        stored = cursors.find_one({"_id": f"{BUSINESS_ID}:{consumer}"})
        since = stored["last_event_id"] if stored else None

    events = list(db[schema.AGENT_LOG].find({"business_id": BUSINESS_ID, "kind": {"$in": RELEVANT}, **_cursor_query(since)})
                  .sort("_id", 1).limit(MAX_EVENTS))
    updates = []
    for e in events:
        for m in messages_for(e, db):
            updates.append({"id": str(e["_id"]), "at": e["at"].isoformat(), **m})

    next_since = str(events[-1]["_id"]) if events else since
    if not peek and events:
        cursors.update_one({"_id": f"{BUSINESS_ID}:{consumer}"},
                           {"$set": {"last_event_id": next_since, "updated_at": datetime.now(timezone.utc)}}, upsert=True)
    return {
        "updates": updates,
        "count": len(updates),
        "next_since": next_since,
        "text": "\n\n".join(u["message"] for u in updates),  # empty string = nothing to send
    }
