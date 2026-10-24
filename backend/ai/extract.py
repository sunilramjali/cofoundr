"""Invoice extraction with a verification loop (MVP1).

Flow: image/PDF → Gemini reads it into structured JSON → deterministic checks
(line items add up to the total, dates valid, required fields present) → if a check
fails, Gemini re-reads the document once with the specific issues → anything still
failing is flagged `needs_review` for the owner. The agent never guesses to make
the numbers balance.

Usage (from the repo root):
    python -m backend.ai.extract data/samples/supplier_clean.png          # extract + verify, print
    python -m backend.ai.extract data/samples/supplier_clean.png --save   # ...and save to MongoDB
"""
import argparse
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from google.genai import errors, types
from pydantic import BaseModel, Field

from backend.ai.gemini import MODEL, get_client, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
BUSINESS_NAME = "GAIA Coffee"
TOLERANCE = 0.01


class ExtractedLineItem(BaseModel):
    description: str
    qty: float
    unit_price: float


class Extraction(BaseModel):
    """Exactly what is printed on the document. Missing values are null, never invented."""
    issuer_name: Optional[str] = Field(None, description="Business that issued (sent) the invoice")
    issuer_email: Optional[str] = None
    recipient_name: Optional[str] = Field(None, description="Business the invoice is billed to")
    recipient_email: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    currency: Optional[str] = Field("GBP", description="ISO code, e.g. GBP")
    line_items: List[ExtractedLineItem] = Field(default_factory=list)
    printed_total: Optional[float] = Field(None, description="The total amount due exactly as printed")
    notes: Optional[str] = Field(None, description="Anything unclear or unreadable")


PROMPT = """You are reading an invoice for a small business's bookkeeping.
Extract exactly what is printed. Rules:
- Report the printed total as printed, even if it doesn't match the line items. Never change numbers to make them balance.
- If a value is missing or unreadable, use null and say why in `notes`.
- Dates as YYYY-MM-DD.
"""

RECHECK = """You previously extracted this invoice as:
{previous}

Automatic checks found these problems:
{issues}

Look at the document again very carefully and re-extract it. Correct any misreadings.
If the document itself is inconsistent (e.g. the printed total really doesn't match the line items),
keep the values exactly as printed and explain the inconsistency in `notes`."""


def _ask_gemini(data: bytes, mime_type: str, prompt: str) -> Extraction:
    response = with_retry(lambda: get_client().models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=data, mime_type=mime_type), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Extraction),
    ))
    if not response.text:  # e.g. blocked by safety filters
        raise ValueError("Gemini returned an empty response.")
    return Extraction.model_validate_json(response.text)


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc) if value else None
    except ValueError:
        return None


def check(ex: Extraction) -> List[str]:
    """Deterministic checks. Returns a list of human-readable issues (empty = all good)."""
    issues = []
    items_sum = round(sum(li.qty * li.unit_price for li in ex.line_items), 2)
    if not ex.line_items:
        issues.append("No line items found.")
    if ex.printed_total is None:
        issues.append("No total amount found.")
    elif abs(items_sum - ex.printed_total) > TOLERANCE:
        issues.append(f"Line items add up to {items_sum:.2f} but the printed total is {ex.printed_total:.2f}.")
    if ex.printed_total is not None and ex.printed_total <= 0:
        issues.append(f"Total is {ex.printed_total:.2f}: looks like a credit note, not something to chase or pay.")
    if (ex.currency or "GBP").upper() != "GBP":
        issues.append(f"Currency is {ex.currency}; CoFoundr assumes GBP.")
    if not ex.invoice_number:
        issues.append("No invoice number found.")
    issue_date, due_date = _parse_date(ex.issue_date), _parse_date(ex.due_date)
    if due_date is None:
        issues.append("Due date missing or not a valid date.")
    if ex.issue_date and issue_date is None:
        issues.append("Issue date is not a valid date.")
    if issue_date and due_date and due_date < issue_date:
        issues.append("Due date is before the issue date.")
    ours = [BUSINESS_NAME.lower() in (name or "").lower() for name in (ex.issuer_name, ex.recipient_name)]
    if not any(ours):
        issues.append(f"Neither issuer nor recipient is {BUSINESS_NAME}.")
    elif all(ours):
        issues.append(f"Both issuer and recipient look like {BUSINESS_NAME}; can't tell who owes whom.")
    return issues


def looks_like_invoice(ex: Extraction) -> bool:
    """False for documents with nothing invoice-like on them (a photo, a menu): those are never saved."""
    return bool(ex.line_items or ex.printed_total or ex.invoice_number)


def extract_invoice(data: bytes, mime_type: str) -> Tuple[Extraction, List[str], int]:
    """Extract, verify, and re-check once if needed. Returns (extraction, remaining issues, attempts)."""
    ex = _ask_gemini(data, mime_type, PROMPT)
    issues = check(ex)
    if not issues:
        return ex, [], 1
    ex = _ask_gemini(data, mime_type, RECHECK.format(previous=ex.model_dump_json(indent=2), issues="\n".join(f"- {i}" for i in issues)))
    return ex, check(ex), 2


def to_invoice_doc(ex: Extraction, issues: List[str], attempts: int, source: str) -> dict:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    receivable = BUSINESS_NAME.lower() in (ex.issuer_name or "").lower()
    counterparty = (ex.recipient_name if receivable else ex.issuer_name) or "Unknown counterparty"
    due_date = _parse_date(ex.due_date)
    items = [li.model_dump() for li in ex.line_items]
    amount = ex.printed_total if ex.printed_total is not None else round(sum(li["qty"] * li["unit_price"] for li in items), 2)
    # 1.0 = clean first time, 0.9 = needed a second look, lower = owner must review
    confidence = 1.0 if attempts == 1 else (0.9 if not issues else round(max(0.1, 0.6 - 0.2 * len(issues)), 2))
    return {
        "business_id": BUSINESS_ID,
        "invoice_number": ex.invoice_number or f"UNKNOWN-{now:%Y%m%d%H%M%S}",
        "direction": "receivable" if receivable else "payable",
        "counterparty": counterparty,
        "counterparty_key": schema.counterparty_key(counterparty),
        "counterparty_email": ex.recipient_email if receivable else ex.issuer_email,
        "amount": amount,
        "currency": ex.currency or "GBP",
        "issue_date": _parse_date(ex.issue_date),
        "due_date": due_date,
        "line_items": items,
        "status": "overdue" if due_date and due_date < today else "open",
        "paid_at": None,
        "source": source,
        "extraction_confidence": confidence,
        "needs_review": bool(issues),
        "verification_issues": issues,
        "extraction_notes": ex.notes,
        "reminders_sent": 0,
        "synthetic": True,
        "created_at": now,
    }


def save_invoice(doc: dict) -> Tuple[dict, bool]:
    """Insert unless the same invoice (same counterparty + number + direction) exists. Returns (stored doc, created)."""
    from pymongo.errors import DuplicateKeyError

    from backend.db.client import get_db

    invoices = get_db()[schema.INVOICES]
    identity = {k: doc[k] for k in ("business_id", "direction", "counterparty_key", "invoice_number")}
    existing = invoices.find_one(identity)
    if existing:
        return existing, False
    try:
        doc["_id"] = invoices.insert_one(doc).inserted_id
    except DuplicateKeyError:  # the same invoice arrived twice at once (e.g. email + upload)
        return invoices.find_one(identity), False
    return doc, True


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and verify an invoice with Gemini.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--save", action="store_true", help="save the result to MongoDB")
    parser.add_argument("--source", default="pdf", choices=["whatsapp_photo", "pdf", "email", "manual"])
    args = parser.parse_args()

    mime_type = mimetypes.guess_type(args.path.name)[0] or "application/octet-stream"
    try:
        ex, issues, attempts = extract_invoice(args.path.read_bytes(), mime_type)
    except errors.ClientError as e:
        raise SystemExit(f"Gemini error {e.code}: {e.message}") from None
    doc = to_invoice_doc(ex, issues, attempts, args.source)

    print(json.dumps({k: v for k, v in doc.items() if k != "line_items"}, default=str, indent=2))
    print(f"\nattempts: {attempts} | confidence: {doc['extraction_confidence']} | needs_review: {doc['needs_review']}")
    if args.save:
        stored, created = save_invoice(doc)
        print(f"{'saved' if created else 'already exists'}: {stored['invoice_number']} ({stored['_id']})")


if __name__ == "__main__":
    main()
