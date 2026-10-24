"""Recording card payments: the hand-off from card-provider inputs (EVO CSV, SumUp) to the database.

For providers that know how much was paid but not what was sold. Input code passes payments in the format at the
top of the "card payments" section of schema.py:

    from backend.inputs import evo
    from backend.payments import record_payments
    parsed = evo.parse(open(path, "rb").read())
    result = record_payments(parsed.payments, origin="EVO CSV", synthetic=parsed.synthetic)

Privacy is enforced here, not trusted to each input: only the allow-listed fields are ever stored, the provider's
transaction id is kept only as a salted hash, and real data is refused in the shared demo database.
Safe to run any number of times (a payment already stored is skipped). Never raises on bad rows: they're
reported in `errors` and the rest are recorded.
"""
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from backend.db import activity, schema
from backend.db.client import check_real_data_allowed, get_db

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
SOURCES = ("evo", "sumup")
DEV_SALT = "cofoundr-synthetic-only"  # good enough to de-duplicate synthetic data; real data needs PAYMENT_ID_SALT


@dataclass
class PaymentSyncResult:
    new: int = 0
    duplicates: int = 0  # already stored (fine: re-imported file or overlapping sync window)
    skipped: int = 0  # not a successful sale or refund (declined, cancelled, chargeback, ...)
    errors: List[str] = field(default_factory=list)  # "row <n>: what's wrong"; those payments were NOT recorded
    revenue: float = 0.0  # sales minus refunds among the new payments, GBP
    fees: float = 0.0

    @property
    def message(self) -> str:
        parts = [f"{self.new} new card payment(s) (£{self.revenue:,.2f}, fees £{self.fees:,.2f})", f"{self.duplicates} already recorded"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped (not a successful sale or refund)")
        if self.errors:
            parts.append(f"{len(self.errors)} rejected")
        return "Card payments: " + ", ".join(parts) + "."


def id_hash(source: str, source_id: str, synthetic: bool) -> str:
    salt = os.environ.get("PAYMENT_ID_SALT")
    if not salt:
        if not synthetic:
            raise ValueError("PAYMENT_ID_SALT isn't set: real payment ids are never stored unsalted. Add it to .env.")
        salt = DEV_SALT
    return hmac.new(salt.encode(), f"{source}:{source_id}".encode(), hashlib.sha256).hexdigest()[:24]


def _money(value, name: str, optional: bool = False) -> Optional[float]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    return round(float(value), 2)


def _when(value, now: datetime) -> tuple:
    """"YYYY-MM-DD" or an ISO timestamp → (London day, UTC datetime or None)."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"paid_at missing or not text: {value!r}")
    if len(value) == 10:
        day = date.fromisoformat(value)
        if day > now.astimezone(LONDON).date():
            raise ValueError(f"date {value} is in the future")
        return day.isoformat(), None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    dt = (dt if dt.tzinfo else dt.replace(tzinfo=LONDON)).astimezone(timezone.utc)
    if dt > now:
        raise ValueError(f"paid_at {value} is in the future")
    return dt.astimezone(LONDON).date().isoformat(), dt


def _one_of(value, allowed: tuple, name: str) -> Optional[str]:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"{name} {value!r} isn't one of {', '.join(allowed)}")
    return value


def _parse(p: dict, synthetic: bool, now: datetime) -> Optional[dict]:
    """One input payment → the stored doc (allow-listed fields only), None to skip it, or raise ValueError(reason)."""
    if p.get("status", "successful") != "successful" or p.get("kind") not in ("sale", "refund"):
        return None
    source = _one_of(p.get("source"), SOURCES, "source")
    if not source:
        raise ValueError("missing source")
    raw_id = str(p.get("source_id") or "").strip()
    if not raw_id:
        raise ValueError("missing source_id")
    if (p.get("currency") or "GBP") != "GBP":
        raise ValueError(f"currency {p.get('currency')!r}: only GBP is supported")
    amount = _money(p.get("amount_gbp"), "amount_gbp")
    if amount <= 0:
        raise ValueError(f"amount {amount:.2f} must be positive (refunds use kind 'refund')")
    fee = _money(p.get("fee_gbp"), "fee_gbp", optional=True)
    if fee is not None and not 0 <= fee <= amount:
        raise ValueError(f"fee {fee:.2f} is outside 0..{amount:.2f}")
    day, paid_at = _when(p.get("paid_at"), now)
    hashed = id_hash(source, raw_id, synthetic)
    return {
        "_id": f"{BUSINESS_ID}:{source}:{hashed}", "business_id": BUSINESS_ID, "source": source, "id_hash": hashed,
        "date": day, "paid_at": paid_at, "amount": amount, "fee": fee, "kind": p["kind"],
        "card_product": _one_of(p.get("card_product"), ("debit", "credit"), "card_product"),
        "card_category": _one_of(p.get("card_category"), ("consumer", "corporate"), "card_category"),
        "region": _one_of(p.get("region"), ("domestic", "eea", "international"), "region"),
        "brand": (str(p["brand"])[:30] if p.get("brand") else None),
        "entry_mode": (str(p["entry_mode"])[:30] if p.get("entry_mode") else None),
        "synthetic": synthetic, "imported_at": now,
    }


def record_payments(payments: Iterable[dict], origin: str, synthetic: bool, now: Optional[datetime] = None) -> PaymentSyncResult:
    """Store successful card sales and refunds (idempotent). One activity-log line per call.
    Raises RealDataRefused before writing anything when real data meets the demo database."""
    db = get_db()
    if not synthetic:
        check_real_data_allowed(db)
    now = now or datetime.now(timezone.utc)
    result = PaymentSyncResult()
    for n, p in enumerate(payments, 1):
        if not isinstance(p, dict):
            result.errors.append(f"row {n}: not a payment")
            continue
        try:
            doc = _parse(p, synthetic, now)
        except (ValueError, TypeError) as e:
            result.errors.append(f"row {n}: {e}")
            continue
        if doc is None:
            result.skipped += 1
            continue
        try:
            db[schema.PAYMENTS].insert_one(doc)
        except DuplicateKeyError:
            result.duplicates += 1
            continue
        result.new += 1
        result.revenue += doc["amount"] if doc["kind"] == "sale" else -doc["amount"]
        result.fees += doc["fee"] or 0
    result.revenue, result.fees = round(result.revenue, 2), round(result.fees, 2)
    if result.new or result.errors:
        activity.log("payments_synced", f"{result.message} [{origin}]", new=result.new, duplicates=result.duplicates,
                     skipped=result.skipped, errors=result.errors[:10], revenue=result.revenue, fees=result.fees)
    return result


def daily_takings(payments: Iterable[dict]) -> dict:
    """Stored payments → {London date: {"revenue", "orders"}}, the same shape as forecast.daily_history (sales minus
    refunds; each sale counts as one order)."""
    days: dict = {}
    for p in payments:
        d = days.setdefault(date.fromisoformat(p["date"]), {"revenue": 0.0, "orders": 0})
        if p["kind"] == "sale":
            d["revenue"] += p["amount"]
            d["orders"] += 1
        else:
            d["revenue"] -= p["amount"]
    return days
