"""Owner decisions on pending approvals. The only place money actions get applied.

Used by both the dashboard and Saint (POST /approve), so the rules are identical:
an action must be `pending`, and only an owner decision moves it on.
Sending is simulated for the demo: an approved reminder increments `reminders_sent`,
an approved supplier payment marks the invoice paid.
"""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from backend.db import activity, schema
from backend.db.client import get_db
from backend.outputs import email

BUSINESS_ID = "gaia-coffee"
DECISIONS = {"approve": "approved", "edit": "edited", "reject": "rejected"}


class DecisionError(Exception):
    pass


def pending() -> list:
    return list(get_db()[schema.APPROVALS].find({"business_id": BUSINESS_ID, "status": "pending"}).sort("ref", 1))


def recent_decided(limit: int = 10) -> list:
    return list(get_db()[schema.APPROVALS].find({"business_id": BUSINESS_ID, "status": {"$ne": "pending"}})
                .sort("decided_at", -1).limit(limit))


def find(ref: Optional[int] = None, approval_id: Optional[str] = None) -> Optional[dict]:
    query = {"business_id": BUSINESS_ID}
    if approval_id:
        query["_id"] = ObjectId(approval_id)
    elif ref is not None:
        query["ref"] = int(ref)
    else:
        return None
    return get_db()[schema.APPROVALS].find_one(query)


def decide(approval: dict, decision: str, via: str, edited_draft: Optional[str] = None) -> dict:
    if decision not in DECISIONS:
        raise DecisionError(f"Unknown decision '{decision}'. Use approve, edit or reject.")
    if approval["status"] != "pending":
        raise DecisionError(f"#{approval['ref']} was already {approval['status']}.")
    if decision == "edit" and not (edited_draft and edited_draft.strip()):
        raise DecisionError("Edit needs the new draft text.")

    db = get_db()
    now = datetime.now(timezone.utc)
    update = {"status": DECISIONS[decision], "decided_via": via, "decided_at": now}
    if decision == "edit":
        update["original_draft"] = approval["draft"]
        update["draft"] = edited_draft.strip()
    result = db[schema.APPROVALS].update_one({"_id": approval["_id"], "status": "pending"}, {"$set": update})
    if result.modified_count == 0:  # someone decided it in the meantime
        raise DecisionError(f"#{approval['ref']} is no longer pending.")

    effect = "no action taken"
    if decision in ("approve", "edit"):
        invoice_id = ObjectId(approval["target_id"])
        if approval["action_type"] == "invoice_reminder":
            effect = email.send_reminder({**approval, **update})  # Jade's output channel; only runs after approval
            db[schema.INVOICES].update_one({"_id": invoice_id}, {"$inc": {"reminders_sent": 1}, "$set": {"last_reminder_at": now}})
        elif approval["action_type"] == "supplier_payment":
            db[schema.INVOICES].update_one({"_id": invoice_id}, {"$set": {"status": "paid", "paid_at": now}})
            effect = "invoice marked paid (simulated payment)"

    actor = "saint" if via == "whatsapp" else "owner"
    activity.log("approval_decided", f"#{approval['ref']} {DECISIONS[decision]} via {via}: {approval['title']} → {effect}",
                 actor=actor, approval_id=str(approval["_id"]), decision=decision)
    return {**approval, **update, "effect": effect}
