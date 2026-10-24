"""The hand-off point between inputs (Jade) and processing (Sunil).

Every input channel (dashboard upload, Gmail, WhatsApp photo via Saint, ...) calls
`ingest_invoice` with the raw document. Everything after that is processing:
Gemini extraction + verification loop → save to MongoDB → activity log → overdue check.
Input code should not call extract/save/overdue directly.
"""
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from google.genai import errors as genai_errors

from backend.ai import overdue
from backend.ai.extract import extract_invoice, save_invoice, to_invoice_doc
from backend.db import activity

Source = Literal["whatsapp_photo", "pdf", "email", "manual"]


@dataclass
class IngestResult:
    ok: bool
    message: str  # one human-readable line, safe to show the owner
    invoice: Optional[dict] = None
    created: bool = False
    needs_review: bool = False
    drafted: List[dict] = field(default_factory=list)  # new pending approvals from the overdue check
    error: Optional[str] = None  # "gemini_unavailable" | "empty" | "too_large"


MAX_BYTES = 10 * 1024 * 1024


def ingest_invoice(data: bytes, mime_type: str, source: Source, origin: str = "") -> IngestResult:
    """Read, check and store one invoice document. `origin` describes where it came from, for the log
    (e.g. "email from billing@x.com: 'Invoice 7802'")."""
    if not data:
        return IngestResult(False, "No file received.", error="empty")
    if len(data) > MAX_BYTES:
        return IngestResult(False, "File too large (max 10 MB).", error="too_large")
    try:
        ex, issues, attempts = extract_invoice(data, mime_type)
    except genai_errors.APIError as e:
        activity.log("ingest_failed", f"Couldn't read a document{f' ({origin})' if origin else ''}: Gemini unavailable ({e.code})",
                     source=source, code=e.code)
        return IngestResult(False, f"Gemini couldn't read it right now ({e.code}). Try again in a minute.", error="gemini_unavailable")

    doc, created = save_invoice(to_invoice_doc(ex, issues, attempts, source))
    if not created:
        return IngestResult(True, f"{doc['invoice_number']} is already in CoFoundr.", invoice=doc)

    verdict = "needs your review: " + issues[0] if issues else f"checked ✓ (attempt {attempts})"
    activity.log("invoice_extracted", f"Read {doc['invoice_number']} from {doc['counterparty']}, £{doc['amount']:.2f}, "
                 f"{verdict}{f' [{origin}]' if origin else ''}", invoice_number=doc["invoice_number"], attempts=attempts, source=source)
    drafted = overdue.run()
    extra = f" {len(drafted)} new action(s) drafted for approval." if drafted else ""
    return IngestResult(True, f"Added {doc['invoice_number']} ({doc['counterparty']}, £{doc['amount']:.2f}): {verdict}.{extra}",
                        invoice=doc, created=True, needs_review=bool(issues), drafted=drafted)
