"""Overdue check + reminder drafts (MVP1).

1. Marks open invoices past their due date as overdue.
2. For each overdue invoice without a pending approval:
   - receivable (customer owes GAIA): Gemini drafts a reminder email, tone escalating
     with `reminders_sent` (friendly → firm → final notice)
   - payable (GAIA owes a supplier): a payment reminder for the owner
3. Saves each draft as a `pending` approval. Nothing is sent: the owner approves first.

Invoices flagged `needs_review` are skipped until the owner has checked them.
If Gemini is unavailable, a template draft is used so the flow still works.

Usage (from the repo root):
    python -m backend.ai.overdue            # run the check, save pending approvals
    python -m backend.ai.overdue --dry-run  # print drafts, write nothing
"""
import argparse
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from google.genai import errors, types
from pydantic import BaseModel

from backend.ai.gemini import MODEL, get_client
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


def _level(reminders_sent: int) -> int:
    return min(reminders_sent, 2)


def due_for_action(inv: dict, today: datetime) -> bool:
    """Receivables wait REMINDER_GAP after the last reminder; supplier bills are always actionable."""
    last = inv.get("last_reminder_at")
    return not (inv["direction"] == "receivable" and last and today - last < REMINDER_GAP)


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


def draft_reminders(invoices: List[dict], today: datetime) -> Dict[str, tuple]:
    """One Gemini call for all receivables. Returns {invoice_number: (Draft, drafted_by)}."""
    if not invoices:
        return {}
    lines = []
    for inv in invoices:
        days = (today - inv["due_date"]).days
        lines.append(f"- {inv['invoice_number']}: customer {inv['counterparty']}, £{inv['amount']:.2f}, "
                     f"due {inv['due_date']:%d %b %Y} ({days} days overdue), reminders already sent: "
                     f"{inv['reminders_sent']}. Tone: {TONES[_level(inv['reminders_sent'])]}.")
    prompt = (
        "You write payment reminder emails for GAIA Coffee, a small mobile coffee truck in East London. "
        "Write one email per invoice below, in British English, plain text, under 120 words, signed 'GAIA Coffee'. "
        "Warm and human, never aggressive; keep the customer relationship. Include the invoice number, amount and due date.\n\n"
        + "\n".join(lines)
    )
    try:
        response = get_client().models.generate_content(
            model=MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Drafts),
        )
        got = {d.invoice_number: (d, "gemini") for d in Drafts.model_validate_json(response.text).drafts}
    except (errors.APIError, ValueError) as e:
        print(f"Gemini unavailable ({getattr(e, 'code', type(e).__name__)}); using template drafts.")
        got = {}
    # Fill any gaps (failed call or a missing invoice in the response) with a template
    return {inv["invoice_number"]: got.get(inv["invoice_number"]) or (_template(inv, (today - inv["due_date"]).days), "template")
            for inv in invoices}


def run(dry_run: bool = False) -> List[dict]:
    from backend.db.client import get_db

    db = get_db()
    invoices, approvals = db[schema.INVOICES], db[schema.APPROVALS]
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if not dry_run:
        flipped = invoices.update_many(
            {"business_id": BUSINESS_ID, "status": "open", "due_date": {"$lt": today}},
            {"$set": {"status": "overdue"}},
        ).modified_count
        if flipped:
            print(f"Marked {flipped} invoice(s) overdue.")

    overdue = list(invoices.find({"business_id": BUSINESS_ID, "status": "overdue", "needs_review": {"$ne": True}}).sort("due_date", 1))
    pending_targets = {a["target_id"] for a in approvals.find({"business_id": BUSINESS_ID, "status": "pending"}, {"target_id": 1})}
    todo = [inv for inv in overdue if str(inv["_id"]) not in pending_targets and due_for_action(inv, today)]
    skipped = len(overdue) - len(todo)

    receivables = [inv for inv in todo if inv["direction"] == "receivable"]
    drafts = draft_reminders(receivables, today)

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

    if not dry_run:
        # Short numbers the owner can reply with on WhatsApp ("approve 3"); backfill any missing
        last = approvals.find_one({"business_id": BUSINESS_ID, "ref": {"$exists": True}}, sort=[("ref", -1)])
        next_ref = (last["ref"] if last else 0) + 1
        for old in approvals.find({"business_id": BUSINESS_ID, "ref": {"$exists": False}}).sort("created_at", 1):
            approvals.update_one({"_id": old["_id"]}, {"$set": {"ref": next_ref}})
            next_ref += 1
        for a in new:
            a["ref"], next_ref = next_ref, next_ref + 1
        if new:
            approvals.insert_many(new)
        from backend.db import activity
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
