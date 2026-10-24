"""Gmail intake: CoFoundr checks the demo inbox on its own and hands every invoice to pipeline.ingest_invoice.

- PDF / image attachments are always read. An email without one is read as text only when its subject mentions
  an invoice, so pitches, newsletters and Google alerts never reach Gemini.
- Each email is handled once: afterwards it gets the Gmail label "CoFoundr/processed" (the inbox is the record;
  nothing here writes MongoDB). An email that failed only because Gemini was busy stays unlabelled, so the next
  check retries it. Messages are fetched with BODY.PEEK, so nothing is marked as read.
- Never raises: every outcome comes back as an InboxResult. A check that found new email is one line in the
  agent log (each invoice also gets the pipeline's own line); a connection or login failure is logged once, not
  every minute.

Needs GMAIL_ADDRESS + GMAIL_APP_PASSWORD (a 16-letter Google app password, not the account password).

    python -m backend.inputs.gmail          # check the inbox once and print what happened
"""
import email
import imaplib
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple

from backend.db import activity
from backend.pipeline import ALLOWED_TYPES, IngestResult, ingest_invoice

IMAP_HOST = "imap.gmail.com"
LABEL = "CoFoundr/processed"
LOOKBACK_DAYS = 14  # the first check doesn't trawl years of mail
MAX_PER_CHECK = 20  # oldest first; the rest wait for the next check
MIN_IMAGE_BYTES = 10_000  # smaller inline images are logos and signatures, not invoice photos
INVOICE_SUBJECT = re.compile(r"\binvoice", re.IGNORECASE)
RETRY = {"gemini_unavailable"}  # leave unlabelled so the next check tries again
FILE_TYPES = ALLOWED_TYPES - {"text/plain"}

_lock = threading.Lock()  # the poller and the "Check inbox now" button never check at the same time
_last_failure: Optional[str] = None  # so a broken login is logged once, not every minute
last_check: Optional[dict] = None  # {"at", "ok", "message"} of the latest finished check, for the dashboard


@dataclass
class EmailOutcome:
    subject: str
    sender: str
    results: List[IngestResult]  # one per document handed to the pipeline; empty = skipped
    note: str  # one line: what happened


@dataclass
class InboxResult:
    ok: bool
    message: str  # one line, safe to show the owner
    emails: List[EmailOutcome] = field(default_factory=list)
    error: Optional[str] = None  # "not_configured" | "busy" | "unreachable" | "login_failed"


class _Text(HTMLParser):
    """HTML email → plain text (for emails without a text/plain part)."""
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        self._skip += tag in ("script", "style")
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def body_text(msg: EmailMessage) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return plain.get_content()
    html_part = msg.get_body(preferencelist=("html",))
    if html_part is None:
        return ""
    parser = _Text()
    parser.feed(html_part.get_content())
    return re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts))


def documents(msg: EmailMessage) -> List[Tuple[bytes, str, str]]:
    """The email's invoice files as [(data, mime_type, name)]: PDFs and real images, not logos."""
    docs = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_type() not in FILE_TYPES:
            continue
        data, name = part.get_payload(decode=True) or b"", part.get_filename()
        if part.get_content_type().startswith("image/"):
            if len(data) < MIN_IMAGE_BYTES or (not name and part.get_content_disposition() != "attachment"):
                continue
        docs.append((data, part.get_content_type(), name or part.get_content_type()))
    return docs


def handle(msg: EmailMessage) -> Tuple[EmailOutcome, bool]:
    """Send one email's invoice(s) to the pipeline. Returns (outcome, retry_later)."""
    subject = str(msg.get("Subject") or "").strip()
    sender = parseaddr(str(msg.get("From") or ""))[1] or "unknown sender"
    origin = f"email from {sender}: '{subject[:80]}'"
    docs = documents(msg)
    if not docs and INVOICE_SUBJECT.search(subject):
        text = body_text(msg).strip()
        if text:  # headers first: the sender's address is often the supplier's billing email
            header = f"From: {msg.get('From', '')}\nTo: {msg.get('To', '')}\nDate: {msg.get('Date', '')}\nSubject: {subject}\n\n"
            docs = [((header + text).encode("utf-8"), "text/plain", "email body")]
    if not docs:
        return EmailOutcome(subject, sender, [], "skipped: no invoice attached or named in the subject"), False
    results = [ingest_invoice(data, mime, source="email", origin=origin if len(docs) == 1 else f"{origin} ({name})")
               for data, mime, name in docs]
    retry = any(r.error in RETRY for r in results)
    return EmailOutcome(subject, sender, results, " ".join(r.message for r in results)), retry


def _credentials() -> Tuple[str, str]:
    return os.environ.get("GMAIL_ADDRESS", "").strip(), os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")


def _connect() -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)


def _failed(error: str, message: str) -> InboxResult:
    global _last_failure
    if message != _last_failure:
        activity.log("gmail_check_failed", message, error=error)
        _last_failure = message
    return InboxResult(False, message, error=error)


def _unprocessed(imap: imaplib.IMAP4) -> List[bytes]:
    """UIDs of recent inbox emails without the processed label, oldest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    typ, data = imap.uid("SEARCH", None, "SINCE", since)
    uids = data[0].split() if typ == "OK" and data and data[0] else []
    if not uids:
        return []
    typ, data = imap.uid("FETCH", b",".join(uids), "(X-GM-LABELS)")
    done = set()
    for line in data if typ == "OK" else []:
        line = line[0] if isinstance(line, tuple) else line
        m = re.search(rb"UID (\d+)", line or b"")
        if m and LABEL.encode() in line:
            done.add(m.group(1))
    return [u for u in uids if u not in done]


def check_inbox(limit: int = MAX_PER_CHECK, connect: Callable[[], imaplib.IMAP4] = None) -> InboxResult:
    """Check the demo inbox once: read new invoices, label every email handled."""
    global last_check
    result = _check_inbox(limit, connect)
    if result.error != "busy":
        last_check = {"at": datetime.now(timezone.utc), "ok": result.ok, "message": result.message}
    return result


def configured() -> bool:
    return all(_credentials())


def _check_inbox(limit: int, connect: Optional[Callable[[], imaplib.IMAP4]]) -> InboxResult:
    global _last_failure
    address, password = _credentials()
    if not address or not password:
        return InboxResult(False, "Gmail intake is off: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.", error="not_configured")
    if not _lock.acquire(blocking=False):
        return InboxResult(False, "Already checking the inbox; try again in a moment.", error="busy")
    try:
        try:
            imap = (connect or _connect)()
        except (OSError, imaplib.IMAP4.error) as e:
            return _failed("unreachable", f"Couldn't reach Gmail ({type(e).__name__}); will try again next check.")
        outcomes = []
        try:
            try:
                imap.login(address, password)
            except imaplib.IMAP4.error:
                return _failed("login_failed", "Gmail refused the login: check GMAIL_APP_PASSWORD (a 16-letter app password).")
            imap.create(f'"{LABEL}"')  # answers NO when it already exists: fine
            imap.select("INBOX")
            for uid in _unprocessed(imap)[:limit]:
                typ, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((part[1] for part in data or [] if isinstance(part, tuple)), None) if typ == "OK" else None
                if raw is None:
                    continue
                outcome, retry = handle(email.message_from_bytes(raw, policy=policy.default))
                outcomes.append(outcome)
                if not retry:
                    imap.uid("STORE", uid, "+X-GM-LABELS", f'"{LABEL}"')
        except (OSError, imaplib.IMAP4.error) as e:
            return _failed("unreachable", f"Lost the connection to Gmail ({type(e).__name__}) after {len(outcomes)} "
                           "email(s); will carry on next check.")
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    finally:
        _lock.release()

    _last_failure = None
    if not outcomes:
        return InboxResult(True, "Inbox checked: nothing new.")
    results = [r for o in outcomes for r in o.results]
    added = [r for r in results if r.created]
    parts = [f"{len(outcomes)} new email(s)", f"{len(added)} invoice(s) added"]
    if any(r.needs_review for r in added):
        parts[-1] += f" ({sum(r.needs_review for r in added)} need(s) your review)"
    if sum(not o.results for o in outcomes):
        parts.append(f"{sum(not o.results for o in outcomes)} not an invoice")
    if sum(not r.ok for r in results):
        parts.append(f"{sum(not r.ok for r in results)} couldn't be read")
    message = "Inbox checked: " + ", ".join(parts) + "."
    activity.log("gmail_checked", message, emails=[{"subject": o.subject[:80], "from": o.sender, "note": o.note[:200]}
                                                   for o in outcomes])
    return InboxResult(all(r.ok for r in results), message, outcomes)


def start_polling(every_seconds: int) -> threading.Event:
    """Check the inbox now and then every `every_seconds` in a background thread. Set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while True:
            try:
                check_inbox()
            except Exception as e:  # never let the poller die quietly
                _failed("unexpected", f"Inbox check crashed ({type(e).__name__}); will try again next check.")
            if stop.wait(every_seconds):
                return
    threading.Thread(target=loop, name="gmail-poller", daemon=True).start()
    return stop


def main() -> None:
    result = check_inbox()
    print(result.message)
    for o in result.emails:
        print(f"  • {o.sender} '{o.subject}': {o.note}")


if __name__ == "__main__":
    main()
