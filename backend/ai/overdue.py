"""Overdue check + reminder drafts (MVP1).

1. Marks open invoices past their due date as overdue.
2. For each overdue invoice without a pending approval:
   - receivable (customer owes GAIA): Gemini drafts a reminder email, tone escalating
     with `reminders_sent` (friendly → firm → final notice)
   - payable (GAIA owes a supplier): a payment reminder for the owner
3. Saves each draft as a `pending` approval. Nothing is sent: the owner approves first.
   Before drafting, the owner's past decisions on this customer and on similar cases are recalled (backend/memory.py,
   Atlas Vector Search): they shape Gemini's draft and are shown with it, but never decide anything.

Invoices flagged `needs_review` are skipped until the owner has checked them.
If Gemini is unavailable, a template draft is used so the flow still works.

Usage (from the repo root):
    python -m backend.ai.overdue            # run the check, save pending approvals
    python -m backend.ai.overdue --dry-run  # print drafts, write nothing
"""
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from google.genai import types
from pydantic import BaseModel

from backend.ai.gemini import MEDIUM, MODEL, get_client, thinking, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
TONES = {
    0: "a friendly first nudge; assume it was simply missed",
    1: "a firm second reminder; mention an earlier reminder was sent and ask for a payment date",
    2: "a polite but clear final notice; mention previous reminders and offer a quick call to resolve it. No legal threats",
}


class Draft(BaseModel):
    invoice_number: str
    subject: str
    body: str


class Drafts(BaseModel):
    drafts: List[Draft]


REMINDER_GAP = timedelta(days=3)  # don't chase the same customer again within 3 days of the last reminder
MAX_REMINDERS = 3  # friendly → firm → final notice; after that a human should call, not another email


def _level(reminders_sent: int) -> int:
    return min(reminders_sent, 2)


def due_for_action(inv: dict, today: datetime) -> bool:
    """Receivables: stop after MAX_REMINDERS, and wait REMINDER_GAP after the last one. Supplier bills: always."""
    if inv["direction"] != "receivable":
        return True
    if inv.get("reminders_sent", 0) >= MAX_REMINDERS:
        return False
    last = inv.get("last_reminder_at")
    return not (last and today - last < REMINDER_GAP)


def _template(inv: dict, days: int) -> Draft:
    level = _level(inv["reminders_sent"])
    opener = ["Just a quick reminder", "Following up on our earlier reminder", "This is a final reminder"][level]
    return Draft(
        invoice_number=inv["invoice_number"],
        subject=f"Invoice {inv['invoice_number']}: payment {days} days overdue",
        body=(f"Hi {inv['counterparty']},\n\n{opener} that invoice {inv['invoice_number']} for £{inv['amount']:.2f} "
              f"was due on {inv['due_date']:%d %b %Y} and is now {days} days overdue. "
              f"Could you let us know when we can expect payment?\n\nThanks,\nGAIA Coffee"),
    )


def draft_reminders(invoices: List[dict], today: datetime, recalled: Optional[Dict[str, List[dict]]] = None) -> Dict[str, tuple]:
    """One Gemini call for all receivables. Returns {invoice_number: (Draft, drafted_by)}.
    `recalled` = {invoice _id: the owner's past decisions (backend/memory.py)}: Gemini follows them."""
    if not invoices:
        return {}
    from backend import memory

    lines = []
    for inv in invoices:
        days = (today - inv["due_date"]).days
        lines.append(f"- {inv['invoice_number']}: customer {inv['counterparty']}, £{inv['amount']:.2f}, "
                     f"due {inv['due_date']:%d %b %Y} ({days} days overdue), reminders already sent: "
                     f"{inv['reminders_sent']}. Tone: {TONES[_level(inv['reminders_sent'])]}.")
        lines += [f"    owner's past decision ({m})" for m in memory.prompt_lines((recalled or {}).get(str(inv["_id"]), []))]
    prompt = (
        "You write payment reminder emails for GAIA Coffee, a small mobile coffee truck in East London. "
        "Write one email per invoice below, in British English, plain text, under 120 words, signed 'GAIA Coffee'. "
        "Warm and human, never aggressive; keep the customer relationship. Include the invoice number, amount and due date. "
        "Where the owner's past decisions are listed, respect them (their reasons, tone and edits: e.g. softer or shorter "
        "if they rejected or shortened a similar reminder), but never mention them, CoFoundr or any other customer in the email.\n\n"
        + "\n".join(lines)
    )
    try:
        response = with_retry(lambda: get_client().models.generate_content(
            model=MODEL, contents=prompt,
            config=types.GenerateContentConfig(thinking_config=thinking(MEDIUM), response_mime_type="application/json", response_schema=Drafts),
        ))
        got = {d.invoice_number: (d, "gemini") for d in Drafts.model_validate_json(response.text).drafts}
    except Exception as e:  # quota, bad JSON, network: the flow must still work on stage
        print(f"Gemini unavailable ({getattr(e, 'code', None) or type(e).__name__}); using template drafts.")
        got = {}
    # Check: an email to one customer must never name another (from the same batch, or a similar case recalled)
    names = {inv["counterparty"] for inv in invoices}
    for inv in invoices:
        number = inv["invoice_number"]
        if number not in got:
            continue
        others = (names | {m.get("counterparty") or "" for m in (recalled or {}).get(str(inv["_id"]), [])}) - {inv["counterparty"]}
        draft = got[number][0]
        own = f"{inv['counterparty']} {memory.about(inv)}"  # its own name and line items are never a leak
        leaked = memory.mentions_other(f"{draft.subject} {draft.body}", own, sorted(n for n in others if n))
        if leaked:
            from backend.db import activity
            activity.log("draft_check_failed", f"Gemini's reminder for {number} named another business; used the template instead",
                         invoice_number=number)
            del got[number]
    # Fill any gaps (failed call, a missing invoice in the response, a failed check) with a template
    return {inv["invoice_number"]: got.get(inv["invoice_number"]) or (_template(inv, (today - inv["due_date"]).days), "template")
            for inv in invoices}


def cancel_stale(db, now: datetime) -> int:
    """Withdraw pending actions whose invoice was paid or removed since they were drafted. Nothing is sent."""
    from bson import ObjectId

    from backend.db import activity

    cancelled = 0
    for a in db[schema.APPROVALS].find({"business_id": BUSINESS_ID, "status": "pending"}):
        inv = db[schema.INVOICES].find_one({"_id": ObjectId(a["target_id"])}, {"status": 1})
        if inv and inv["status"] != "paid":
            continue
        why = "invoice already paid" if inv else "invoice no longer exists"
        if db[schema.APPROVALS].update_one({"_id": a["_id"], "status": "pending"}, {"$set": {
                "status": "cancelled", "decided_via": "system", "decided_at": now, "cancel_reason": why}}).modified_count:
            cancelled += 1
            activity.log("approval_cancelled", f"#{a['ref']} withdrawn: {a['title']} ({why}; nothing sent)", approval_id=str(a["_id"]))
    return cancelled


def run(dry_run: bool = False, only=None) -> List[dict]:
    """`only` = one invoice's _id: the check (overdue flag + draft) touches that invoice and nothing else."""
    from backend.db.client import get_db

    db = get_db()
    invoices, approvals = db[schema.INVOICES], db[schema.APPROVALS]
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    scope = {"business_id": BUSINESS_ID, **({"_id": only} if only is not None else {})}

    if not dry_run:
        flipped = invoices.update_many(
            {**scope, "status": "open", "due_date": {"$lt": today}},
            {"$set": {"status": "overdue"}},
        ).modified_count
        if flipped:
            print(f"Marked {flipped} invoice(s) overdue.")
        cancelled = 0 if only is not None else cancel_stale(db, now)
        if cancelled:
            print(f"Withdrew {cancelled} pending action(s) whose invoice is paid or gone.")

    overdue = list(invoices.find({**scope, "status": "overdue", "needs_review": {"$ne": True}}).sort("due_date", 1))
    pending_targets = {a["target_id"] for a in approvals.find({"business_id": BUSINESS_ID, "status": "pending"}, {"target_id": 1})}
    todo = [inv for inv in overdue if str(inv["_id"]) not in pending_targets and due_for_action(inv, today)]
    skipped = len(overdue) - len(todo)

    from backend import memory

    if not dry_run:
        memory.settle(db, now)  # write back what happened after earlier chases (paid since?) before recalling them
    recalled = {}
    for inv in todo:
        kind = "invoice_reminder" if inv["direction"] == "receivable" else "supplier_payment"
        level = _level(inv["reminders_sent"]) if kind == "invoice_reminder" else None
        recalled[str(inv["_id"])] = memory.recall(db, memory.case(inv, kind, (today - inv["due_date"]).days, level))

    receivables = [inv for inv in todo if inv["direction"] == "receivable"]
    drafts = draft_reminders(receivables, today, recalled)

    new = []
    for inv in todo:
        days = (today - inv["due_date"]).days
        base = {
            "business_id": BUSINESS_ID,
            "target_id": str(inv["_id"]),
            "invoice_number": inv["invoice_number"],
            "counterparty": inv["counterparty"],
            "amount": inv["amount"],
            "days_overdue": days,
            "status": "pending",
            "decided_via": None,
            "created_at": now,
            "decided_at": None,
            "memories": recalled[str(inv["_id"])],
            "synthetic": True,
        }
        if inv["direction"] == "receivable":
            draft, drafted_by = drafts[inv["invoice_number"]]
            level = _level(inv["reminders_sent"])
            new.append({**base,
                        "action_type": "invoice_reminder",
                        "title": f"Chase {inv['counterparty']}: £{inv['amount']:.2f}, {days} days overdue",
                        "reason": f"Customer invoice {days} days overdue; {inv['reminders_sent']} reminder(s) sent so far → "
                                  f"{['friendly nudge', 'firm reminder', 'final notice'][level]}.",
                        "escalation_level": level,
                        "to_email": inv.get("counterparty_email"),
                        "draft_subject": draft.subject,
                        "draft": draft.body,
                        "drafted_by": drafted_by})
        else:
            new.append({**base,
                        "action_type": "supplier_payment",
                        "title": f"Pay {inv['counterparty']}: £{inv['amount']:.2f}, {days} days overdue",
                        "reason": f"Supplier invoice {days} days overdue; paying late risks supply to the truck.",
                        "escalation_level": None,
                        "to_email": None,
                        "draft_subject": None,
                        "draft": f"Pay {inv['counterparty']} £{inv['amount']:.2f} for invoice {inv['invoice_number']} "
                                 f"(due {inv['due_date']:%d %b %Y}).",
                        "drafted_by": "rule"})

    for a in new:  # the owner's own words about this customer/supplier come first in the reason they read
        said = next((m["note"] for m in a["memories"] if m["same_counterparty"] and m.get("note")), None)
        if said:
            a["reason"] += f" Last time you said: “{said}”."

    if not dry_run:
        from pymongo.errors import DuplicateKeyError

        from backend.db import activity
        from backend.db.client import next_refs

        # Short numbers the owner replies with on WhatsApp ("approve 3"), reserved atomically
        saved = []
        for a, ref in zip(new, next_refs(db, BUSINESS_ID, len(new))):
            a["ref"] = ref
            try:
                approvals.insert_one(a)
                saved.append(a)
            except DuplicateKeyError:  # another check drafted this invoice at the same moment; keep theirs
                pass
        new = saved
        fallback = sum(a["drafted_by"] == "template" for a in new)
        if fallback:  # surfaced, never silent: the owner sees these drafts weren't written by Gemini
            activity.log("gemini_fallback", f"Gemini didn't draft {fallback} reminder(s) (unavailable after retries or skipped them); used the template",
                         refs=[a["ref"] for a in new if a["drafted_by"] == "template"])
        activity.log("overdue_check", f"Overdue check: {len(overdue)} overdue, {len(new)} new action(s) drafted for approval",
                     refs=[a["ref"] for a in new])
    print(f"{len(overdue)} overdue invoice(s): {len(new)} new approval(s) "
          f"{'drafted (dry run, not saved)' if dry_run else 'saved as pending'}, {skipped} already pending or reminded recently.")
    return new


def main() -> None:
    parser = argparse.ArgumentParser(description="Find overdue invoices and draft actions for owner approval.")
    parser.add_argument("--dry-run", action="store_true", help="print drafts, write nothing")
    args = parser.parse_args()
    for a in run(dry_run=args.dry_run):
        print(f"\n▶ #{a.get('ref', '?')} {a['title']}  [{a['action_type']}, drafted by {a['drafted_by']}]\n  why: {a['reason']}")
        if a["draft_subject"]:
            print(f"  to: {a['to_email']}\n  subject: {a['draft_subject']}")
        print("  " + a["draft"].replace("\n", "\n  "))


if __name__ == "__main__":
    main()
