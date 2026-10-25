"""The hand-off point between inputs (Jade) and processing (Sunil).

Every input channel (dashboard upload, Gmail, WhatsApp photo via Twilio, ...) calls
`ingest_invoice` with the raw document. Everything after that is processing:
Gemini extraction + verification loop → duplicate check (Atlas Search) → save to MongoDB → activity log → price check
(supplier bills) → overdue check.
Input code should not call extract/save/overdue directly. It never raises: every
outcome comes back as an IngestResult with a message safe to show the owner.
"""
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from google.genai import errors as genai_errors

from backend.ai import overdue
from backend.ai.extract import extract_invoice, looks_like_invoice, save_invoice, to_invoice_doc
from backend.db import activity, search
from backend.db.client import get_db

Source = Literal["whatsapp_photo", "pdf", "email", "manual"]

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
                 "text/plain"}  # text: invoices written in an email body (Gmail intake)
RETRYABLE = {429, 500, 502, 503, 504}  # Gemini busy/over quota → "try again"; other codes → can't read this file


@dataclass
class IngestResult:
    ok: bool
    message: str  # one human-readable line, safe to show the owner
    invoice: Optional[dict] = None
    created: bool = False
    needs_review: bool = False
    drafted: List[dict] = field(default_factory=list)  # new pending approvals from the overdue check
    # "empty" | "too_large" | "unsupported_type" | "gemini_unavailable" | "unreadable" | "not_invoice"
    error: Optional[str] = None


def _price_note(doc: dict) -> str:
    """Compare a checked supplier bill's prices with earlier bills; log it and return one line for the owner (or "").
    Never blocks the ingest: a failure is logged and the invoice is still saved."""
    if doc.get("direction") != "payable" or doc.get("needs_review"):
        return ""
    try:
        check = search.price_check(get_db(), doc)
    except Exception as e:
        activity.log("price_check_failed", f"Couldn't compare prices on {doc['invoice_number']} ({type(e).__name__})",
                     invoice_id=str(doc["_id"]))
        return ""
    if not check.comparisons:
        return ""
    lines = check.lines()
    activity.log("price_checked", lines[0] if lines else
                 f"Checked {len(check.comparisons)} price(s) on {doc['invoice_number']} against earlier bills: in line",
                 invoice_id=str(doc["_id"]), invoice_number=doc["invoice_number"], mode=check.mode,
                 comparisons=check.comparisons, preferred=check.preferred)
    return lines[0] if lines else ""


def _duplicate_issue(doc: dict) -> str:
    """Flag a probable duplicate for the owner's check ("EDC 7781" when EDC-7781 is already in). Never blocks the ingest:
    a failure is logged and the invoice goes in as normal."""
    try:
        dup = search.possible_duplicate(get_db(), doc)
    except Exception as e:
        activity.log("duplicate_check_failed", f"Couldn't check {doc['invoice_number']} for duplicates ({type(e).__name__})")
        return ""
    if not dup:
        return ""
    doc["possible_duplicate_of"] = str(dup["_id"])
    return (f"looks like a duplicate of {dup['invoice_number']} from {dup['counterparty']} (same number and amount, "
            f"£{dup['amount']:.2f}): check it before paying or chasing.")


def _fail(error: str, message: str, source: str, origin: str, **detail) -> IngestResult:
    where = f" ({origin})" if origin else ""
    activity.log("ingest_failed", f"Couldn't add a document{where}: {message}", source=source, error=error, **detail)
    return IngestResult(False, message, error=error)


def ingest_invoice(data: bytes, mime_type: str, source: Source, origin: str = "") -> IngestResult:
    """Read, check and store one invoice document. `origin` describes where it came from, for the log
    (e.g. "email from billing@x.com: 'Invoice 7802'")."""
    if not data:
        return IngestResult(False, "No file received.", error="empty")
    if len(data) > MAX_BYTES:
        return IngestResult(False, "File too large (max 10 MB).", error="too_large")
    mime_type = (mime_type or "").split(";")[0].strip().lower()
    if mime_type not in ALLOWED_TYPES:
        return IngestResult(False, f"Can't read {mime_type or 'that file type'}: send a PDF or a photo (PNG/JPEG).",
                            error="unsupported_type")

    try:
        ex, issues, attempts = extract_invoice(data, mime_type)
    except genai_errors.APIError as e:
        if e.code in RETRYABLE:
            return _fail("gemini_unavailable", f"Gemini couldn't read it right now ({e.code}). Try again in a minute.",
                         source, origin, code=e.code)
        return _fail("unreadable", f"Couldn't read this file as an invoice ({e.code}).", source, origin, code=e.code)
    except Exception as e:  # bad/empty model output, network errors: never crash an input channel
        return _fail("unreadable", "Couldn't read this file as an invoice. Try a clearer photo or the PDF.",
                     source, origin, reason=type(e).__name__)

    if not looks_like_invoice(ex):
        return _fail("not_invoice", "That doesn't look like an invoice (no total, line items or invoice number). Nothing saved.",
                     source, origin)

    doc = to_invoice_doc(ex, issues, attempts, source)
    duplicate = _duplicate_issue(doc)
    if duplicate:  # asked, not guessed: held for review, so it's never chased or paid twice
        issues = [duplicate] + list(issues)
        doc.update(needs_review=True, verification_issues=issues)
    doc, created = save_invoice(doc)
    if not created:
        return IngestResult(True, f"{doc['invoice_number']} from {doc['counterparty']} is already in CoFoundr.", invoice=doc)

    verdict = "needs your review: " + issues[0].rstrip(".") if issues else f"checked ✓ (attempt {attempts})"
    activity.log("invoice_extracted", f"Read {doc['invoice_number']} from {doc['counterparty']}, £{doc['amount']:.2f}, "
                 f"{verdict}{f' [{origin}]' if origin else ''}",
                 invoice_id=str(doc["_id"]), invoice_number=doc["invoice_number"], attempts=attempts, source=source)
    note = _price_note(doc)
    drafted = overdue.run()
    extra = f" {len(drafted)} new action(s) drafted for approval." if drafted else ""
    return IngestResult(True, f"Added {doc['invoice_number']} ({doc['counterparty']}, £{doc['amount']:.2f}): {verdict}.{extra}"
                        + (f" {note}" if note else ""),
                        invoice=doc, created=True, needs_review=bool(issues), drafted=drafted)
