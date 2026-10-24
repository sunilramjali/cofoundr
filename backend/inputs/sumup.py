"""SumUp: card payments from the transaction history, read-only, mapped to the backend.payments input format.

SumUp's history says how much was paid and when, not what was sold, so it's stored like EVO's payments.
Read-only: only GET /v0.1/me/transactions/history is called; an OAuth token needs just `transactions.history`.

Privacy: card details, the staff user (an email), product summaries and ids beyond the transaction id (hashed by
backend.payments) are never copied (see docs/privacy.md).

Env: SUMUP_ACCESS_TOKEN, SUMUP_ENV=sandbox|production (default sandbox: a SumUp test account counts as synthetic).

    python -m backend.inputs.sumup --days 7 --dry-run
"""
import argparse
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from backend.inputs import fetch

HISTORY = "https://api.sumup.com/v0.1/me/transactions/history"
PAGE = 100
KINDS = {"PAYMENT": "sale", "REFUND": "refund"}  # CHARGE_BACK and the rest are skipped
ENTRY = {"contactless": "contactless", "chip": "chip", "magstripe": "swipe", "manual_entry": "manual"}


class SumUpClient:
    def __init__(self, token: Optional[str] = None, env: Optional[str] = None, get=fetch.get_json):
        self.token = token or os.environ.get("SUMUP_ACCESS_TOKEN")
        if not self.token:
            raise fetch.FetchError("SumUp isn't connected: set SUMUP_ACCESS_TOKEN (see .env.example).")
        self.env = env or os.environ.get("SUMUP_ENV", "sandbox")
        if self.env not in ("sandbox", "production"):
            raise fetch.FetchError(f"SUMUP_ENV must be sandbox or production, not {self.env!r}")
        self._get = get

    @property
    def synthetic(self) -> bool:
        return self.env == "sandbox"

    def iter_transactions(self, since: datetime, until: datetime) -> Iterator[dict]:
        params = {"oldest_time": since.isoformat(), "newest_time": until.isoformat(), "limit": PAGE, "order": "ascending"}
        while True:
            page = self._get(HISTORY, params, "SumUp", headers={"Authorization": f"Bearer {self.token}"})
            yield from page.get("items", [])
            nxt = next((link.get("href") for link in page.get("links", []) if link.get("rel") == "next"), None)
            if not nxt:
                return
            params = dict(urllib.parse.parse_qsl(nxt.split("?", 1)[-1]))  # SumUp gives the next page as a query string


def to_payment(t: dict) -> dict:
    """One SumUp history item → backend.payments input format (allow-listed fields only)."""
    return {"source": "sumup", "source_id": str(t.get("id") or t.get("transaction_code") or ""),
            "paid_at": t.get("timestamp"), "amount_gbp": t.get("amount"), "fee_gbp": None,
            "currency": t.get("currency") or "GBP", "kind": KINDS.get(t.get("type"), "other"),
            "status": "successful" if t.get("status") == "SUCCESSFUL" else (t.get("status") or "unknown").lower(),
            "card_product": None, "card_category": None, "region": None,
            "brand": (t.get("card_type") or "").title() or None,
            "entry_mode": ENTRY.get((t.get("entry_mode") or "").lower())}


def sync(client: Optional[SumUpClient] = None, days: int = 7, now: Optional[datetime] = None, dry_run: bool = False):
    client = client or SumUpClient()
    now = now or datetime.now(timezone.utc)
    payments = [to_payment(t) for t in client.iter_transactions(now - timedelta(days=days), now)]
    if dry_run:
        return None, payments
    from backend.payments import record_payments
    return record_payments(payments, origin=f"SumUp ({client.env}, last {days} days)", synthetic=client.synthetic, now=now), payments


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Read SumUp card payments (read-only) into CoFoundr.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result, payments = sync(days=args.days, dry_run=args.dry_run)
    print(result.message if result else f"{len(payments)} transaction(s) read (dry run)")


if __name__ == "__main__":
    main()
