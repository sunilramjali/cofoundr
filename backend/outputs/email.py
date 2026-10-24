"""Sending approved reminder emails (owner: Jade).

`approvals.decide` calls `send_reminder` only AFTER the owner approves (or edits and approves)
an `invoice_reminder`. Nothing is ever sent without that confirmation.

TODO(Jade, MVP2): send for real, e.g. via Gmail SMTP from the demo inbox
(GMAIL_ADDRESS + GMAIL_APP_PASSWORD). Until then this simulates the send.
Return a short description of what happened; it's shown to the owner and logged.
"""


def send_reminder(approval: dict) -> str:
    return f"reminder sent to {approval.get('to_email')} (simulated)"
