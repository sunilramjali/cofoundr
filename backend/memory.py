"""Owner-decision memory (MVP2): CoFoundr remembers how the owner decided, and why, and recalls it next time.

A solo owner shouldn't have to explain the same thing twice ("don't push Hackney Hub, they always pay, just late").
1. Remember: every decision on an approval (approve / edit / reject, with the owner's optional reason) becomes one
   `memories` document. Atlas embeds its `search_text` itself (Automated Embedding, Voyage): no embedding code here.
2. Recall: before a reminder or payment is drafted, the same customer's or supplier's own history is read directly,
   then the most similar other cases are found by meaning (Atlas Vector Search, `search.similar_memories`).
   They go into Gemini's drafting prompt and are shown with the draft.
3. Outcome write-back: when a chased invoice is later paid, or still unpaid when the next reminder is approved, the
   memory records it ("paid 3 days later"), so the owner sees what worked last time.

Recall only informs. It never decides, skips, sends or pays anything: every action still waits for the owner.

Usage (from the repo root):
    python -m backend.memory --list               # what CoFoundr remembers, newest first
    python -m backend.memory --recall GAIA-1041   # what it would recall for that invoice
    python -m backend.memory --backfill           # remember earlier decisions (from decided approvals; no reasons)
"""
import argparse
import re
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.db import schema, search

BUSINESS_ID = "gaia-coffee"
MAX_NOTE = 300  # characters of the owner's reason kept
MAX_ABOUT = 160
RECALL = 3  # memories shown per case: enough to remind, few enough to read on a phone
OWN_FIRST = 2  # of which at most this many from the same customer/supplier, so a similar case can still show
DECIDED = {"approved", "edited", "rejected"}
TONES = ["friendly nudge", "firm reminder", "final notice"]


def clean_note(note: Optional[str]) -> Optional[str]:
    note = re.sub(r"\s+", " ", note or "").strip()[:MAX_NOTE]
    return note or None


def about(invoice: Optional[dict]) -> str:
    """What the invoice was for: "Coffee catering, 40 people; Pastries"."""
    items = "; ".join(li.get("description", "") for li in (invoice or {}).get("line_items") or [] if li.get("description"))
    return items[:MAX_ABOUT]


def case(invoice: dict, action_type: str, days_overdue: Optional[int] = None, escalation_level: Optional[int] = None) -> dict:
    """A new case to recall memories for, in the same shape as a memory (without a decision)."""
    return {"counterparty": invoice.get("counterparty") or "", "action_type": action_type,
            "counterparty_key": invoice.get("counterparty_key") or schema.counterparty_key(invoice.get("counterparty")),
            "about": about(invoice), "amount": invoice.get("amount") or 0.0, "days_overdue": days_overdue,
            "escalation_level": escalation_level}


def _edit_summary(before: Optional[str], after: Optional[str]) -> Optional[str]:
    if not before or not after or before.strip() == after.strip():
        return None
    a, b = len(before.split()), len(after.split())
    if b < a * 0.8:
        return f"shortened the draft from {a} to {b} words"
    if b > a * 1.2:
        return f"lengthened the draft from {a} to {b} words"
    return "reworded the draft"


def remember(db: Database, approval: dict, decided: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Store one decision (once per approval). `decided` = the approval after the decision ("status", "decided_via",
    "owner_note", "draft"). Also writes the outcome of earlier reminders for the same invoice: still unpaid, chased again."""
    now = now or datetime.now(timezone.utc)
    decision = decided.get("status")
    if decision not in DECIDED or db[schema.MEMORIES].find_one({"approval_id": str(approval["_id"])}, {"_id": 1}):
        return None  # not a decision, or already remembered: no side effects (a re-run must not rewrite outcomes)
    try:
        invoice = db[schema.INVOICES].find_one({"_id": ObjectId(approval["target_id"])}, {"line_items": 1, "counterparty_key": 1})
    except (InvalidId, TypeError, KeyError):
        invoice = None
    doc = {
        "business_id": approval.get("business_id", BUSINESS_ID),
        "approval_id": str(approval["_id"]),
        "ref": approval.get("ref"),
        "action_type": approval.get("action_type"),
        "target_id": approval.get("target_id"),
        "invoice_number": approval.get("invoice_number"),
        "counterparty": approval.get("counterparty") or "",
        "counterparty_key": (invoice or {}).get("counterparty_key") or schema.counterparty_key(approval.get("counterparty")),
        "amount": approval.get("amount") or 0.0,
        "about": about(invoice),
        "days_overdue": approval.get("days_overdue"),
        "escalation_level": approval.get("escalation_level"),
        "decision": decision,
        "via": decided.get("decided_via") or "",
        "note": clean_note(decided.get("owner_note")),
        "edit": _edit_summary(approval.get("draft"), decided.get("draft")) if decision == "edited" else None,
        "outcome": None,
        "at": now,
        "synthetic": True,
    }
    doc["search_text"] = schema.memory_search_text(doc)
    if doc["action_type"] == "invoice_reminder" and decision in ("approved", "edited"):
        for earlier in db[schema.MEMORIES].find({"target_id": doc["target_id"], "outcome": None, "at": {"$lt": now},
                                                 "approval_id": {"$ne": doc["approval_id"]},
                                                 "decision": {"$in": ["approved", "edited"]}}, {"at": 1}):
            db[schema.MEMORIES].update_one({"_id": earlier["_id"], "outcome": None}, {"$set": {"outcome": {
                "result": "chased_again", "days": max(0, (now - _aware(earlier["at"])).days), "at": now}}})
    try:
        db[schema.MEMORIES].insert_one(doc)
    except DuplicateKeyError:  # already remembered (a retried decision, or the backfill ran twice)
        return None
    return doc


def settle(db: Database, now: Optional[datetime] = None) -> int:
    """Write back what happened after a chase: the invoice has since been paid. Returns how many memories it updated."""
    now = now or datetime.now(timezone.utc)
    settled = 0
    for m in db[schema.MEMORIES].find({"business_id": BUSINESS_ID, "action_type": "invoice_reminder", "outcome": None,
                                       "decision": {"$in": ["approved", "edited"]}}, {"target_id": 1, "at": 1}):
        try:
            inv = db[schema.INVOICES].find_one({"_id": ObjectId(m["target_id"])}, {"status": 1, "paid_at": 1})
        except (InvalidId, TypeError):
            continue
        if inv and inv.get("status") == "paid":
            paid_at = _aware(inv.get("paid_at") or now)
            days = max(0, (paid_at - _aware(m["at"])).days)
            settled += db[schema.MEMORIES].update_one({"_id": m["_id"], "outcome": None}, {"$set": {"outcome": {
                "result": "paid", "days": days, "at": now}}}).modified_count
    return settled


def recall(db: Database, new_case: dict, limit: int = RECALL, details: Optional[dict] = None) -> List[dict]:
    """The owner's past decisions that matter for a new case, as RecalledMemory dicts: this customer's/supplier's own
    (newest first, read directly), then similar cases by meaning that taught something (a no, an edit, a reason or an
    outcome). `details` (optional) gets "mode": how similar cases were found ("hybrid" = Vector Search, "basic" = words).
    Never raises: a broken memory must never stop a draft."""
    try:
        key = new_case.get("counterparty_key")
        own = list(db[schema.MEMORIES].find({"business_id": BUSINESS_ID, "counterparty_key": key,
                                             "action_type": new_case.get("action_type")}, search.MEMORY_FIELDS)
                   .sort("at", -1).limit(OWN_FIRST)) if key else []
        out = [_recalled(m, True) for m in own]
        if len(out) < limit:
            similar = search.similar_memories(db, schema.memory_search_text(new_case), new_case.get("action_type"),
                                              limit=limit + 3, exclude_key=key)
            if details is not None:
                details["mode"] = similar.mode
            taught = [m for m in similar.hits if m.get("decision") != "approved" or m.get("note") or m.get("outcome")]
            out += [_recalled(m, False) for m in taught[:limit - len(out)]]
        return out
    except Exception as e:  # noqa: BLE001 (logged, never silent)
        from backend.db import activity
        activity.log("memory_failed", f"Couldn't recall past decisions ({type(e).__name__}); drafted without them",
                     error=type(e).__name__)
        return []


def _recalled(m: dict, same: bool) -> dict:
    return {"line": line(m), "anonymous_line": line(m, anonymous=not same), "decision": m.get("decision"),
            "note": m.get("note"), "same_counterparty": same,
            "score": None if same else round(m.get("score") or 0.0, 3), "at": m.get("at")}


def line(m: dict, anonymous: bool = False) -> str:
    """"24 Oct: you said no to a firm reminder to Hackney Hub (£450.00, 12 days overdue): “they're a friend”. Paid 3 days later."
    anonymous=True: "another customer" instead of the name, also inside the owner's note (for a prompt about someone else)."""
    name = m.get("counterparty") or ""
    who = ("another customer" if m.get("action_type") == "invoice_reminder" else "another supplier") if anonymous else (name or "them")
    if anonymous and name and m.get("note"):
        m = {**m, "note": re.sub(re.escape(name), "them", m["note"], flags=re.IGNORECASE)}
    if m.get("action_type") == "invoice_reminder":
        level = m.get("escalation_level")
        what = f"a {TONES[level]} to {who}" if level in (0, 1, 2) else f"chasing {who}"
    else:
        what = f"paying {who}"
    verb = {"approved": "you approved", "edited": "you rewrote", "rejected": "you said no to"}.get(m.get("decision"), "you decided on")
    facts = [f"£{m.get('amount') or 0:.2f}"] + ([f"{m['days_overdue']} days overdue"] if m.get("days_overdue") is not None else [])
    text = f"{_aware(m['at']):%d %b}: {verb} {what} ({', '.join(facts)})"
    if m.get("note"):
        text += f": “{m['note']}”"
    if m.get("edit"):
        text += f"; you {m['edit']}"
    outcome = m.get("outcome") or {}
    if outcome.get("result") == "paid":
        text += f". Paid {_days(outcome['days'])} later"
    elif outcome.get("result") == "chased_again":
        text += f". Still unpaid {_days(outcome['days'])} later, so it was chased again"
    return text + "."


def prompt_lines(memories: List[dict]) -> List[str]:
    """For Gemini's drafting prompt: what the owner decided before (their words in quotes). A similar case about someone
    else is named "another customer": this email must never be able to leak another customer's name."""
    return [("same customer: " + m["line"]) if m["same_counterparty"] else
            ("a similar case: " + (m.get("anonymous_line") or "another customer's case")) for m in memories]


def backfill(db: Database) -> int:
    """Remember decisions made before memory existed (no reasons: those were never asked for)."""
    n = 0
    for a in db[schema.APPROVALS].find({"business_id": BUSINESS_ID, "status": {"$in": sorted(DECIDED)}}).sort("decided_at", 1):
        if remember(db, a, a, now=_aware(a.get("decided_at") or a.get("created_at") or datetime.now(timezone.utc))):
            n += 1
    return n


def _days(n: int) -> str:
    return "the same day" if n == 0 else f"{n} day{'s' if n != 1 else ''}"


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def main() -> None:
    from backend.db.client import get_db

    parser = argparse.ArgumentParser(description="What CoFoundr remembers about the owner's decisions.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--recall", metavar="INVOICE_NUMBER")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    db = get_db()
    if args.backfill:
        print(f"Remembered {backfill(db)} earlier decision(s); settled {settle(db)} outcome(s).")
    if args.list:
        for m in db[schema.MEMORIES].find({"business_id": BUSINESS_ID}).sort("at", -1).limit(30):
            print(f"  #{m.get('ref')}  {line(m)}")
    if args.recall:
        inv = db[schema.INVOICES].find_one({"business_id": BUSINESS_ID, "invoice_number": args.recall})
        if not inv:
            raise SystemExit(f"No invoice {args.recall}")
        kind = "invoice_reminder" if inv["direction"] == "receivable" else "supplier_payment"
        for m in recall(db, case(inv, kind, escalation_level=min(inv.get("reminders_sent", 0), 2) if kind == "invoice_reminder" else None)):
            print(f"  [{'same' if m['same_counterparty'] else 'similar ' + str(m['score'])}] {m['line']}")


if __name__ == "__main__":
    main()
