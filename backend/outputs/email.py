"""Sending approved reminder emails (owner: Jade).

`approvals.decide` calls `send_reminder` only AFTER the owner approves (or edits and approves)
an `invoice_reminder`. Nothing is ever sent without that confirmation.

- Real email via Gmail SMTP from the demo inbox (GMAIL_ADDRESS + GMAIL_APP_PASSWORD, the same app password as the
  inbox check). Without them (local, tests) the send is simulated and says so.
- Demo safety: every reminder goes to ONE inbox we chose, REMINDER_INBOX (default: the demo inbox itself), never to the
  customer's address on the invoice. The email names who it's really for. The customers are synthetic anyway.
- Each email carries `X-CoFoundr: reminder`, so the inbox check never reads CoFoundr's own reminders as invoices.
- A failed send raises: `approvals.decide` puts the approval back in the queue instead of pretending it went.
Returns a short description of what happened; it's shown to the owner and logged.
"""
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

SMTP_HOST = "smtp.gmail.com"
TIMEOUT = 20
MARKER = "X-CoFoundr"  # our own emails; backend/inputs/gmail.py skips them
BUSINESS = "GAIA Coffee"


def _config():
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    inbox = os.environ.get("REMINDER_INBOX", "").strip() or sender
    return sender, password, inbox


def _smtp() -> smtplib.SMTP:
    return smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=TIMEOUT)


def build(approval: dict, sender: str, inbox: str) -> EmailMessage:
    meant_for = approval.get("to_email") or "the customer (no email on the invoice)"
    msg = EmailMessage()
    msg["From"] = formataddr((BUSINESS, sender))
    msg["To"] = inbox
    msg["Subject"] = approval.get("draft_subject") or f"Payment reminder from {BUSINESS}"
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1] or "cofoundr.local")
    msg[MARKER] = "reminder"
    msg.set_content(f"{(approval.get('draft') or '').strip()}\n\n"
                    f"--\nSent by CoFoundr for {BUSINESS} after the owner approved #{approval.get('ref')}.\n"
                    f"Demo: written for {meant_for}, delivered to this demo inbox instead. All data is synthetic.\n")
    return msg


def send_reminder(approval: dict) -> str:
    sender, password, inbox = _config()
    if not (sender and password):
        return f"reminder sent to {approval.get('to_email')} (simulated)"
    with _smtp() as smtp:  # any failure raises: the approval goes back in the queue
        smtp.login(sender, password)
        smtp.send_message(build(approval, sender, inbox))
    return f"reminder emailed to the demo inbox ({inbox}), written for {approval.get('to_email') or 'the customer'}"
