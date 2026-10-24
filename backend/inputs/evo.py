"""EVO card payments: the transactions CSV exported from EVO's portal (Business Resource Centre), uploaded by hand.

EVO has no read-only API for merchants, so the owner downloads "Transactions" and uploads it (dashboard: Sales page,
or the command below). Returns data only: storing it is backend.payments' job.

Privacy: only the allow-listed columns in KEEP are read. Card number, auth code, card-network references, issuer
country, outlet/terminal ids and the merchant name are never copied into a payment (see docs/privacy.md).
EVO gives the date of each payment but not the time.

    python -m backend.inputs.evo ~/Documents/private/transactions.csv --dry-run   # check the file, store nothing
    MONGODB_DB=gaia-real python -m backend.inputs.evo ~/Documents/private/transactions.csv
"""
import argparse
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

SYNTHETIC_MARK = "(SYNTHETIC)"  # data/samples' demo export ends its merchant name with this; real exports never do
REQUIRED = ("TransactionID", "Date", "Amount", "ChargeDesc", "Transaction currency")
KEEP = REQUIRED + ("MSCAmount", "Product", "CardType", "Region", "CardBrandDesc")  # everything else is never read
REGIONS = {"domestic": "domestic", "intra": "eea", "inter": "international"}
NOT_EVO = "This doesn't look like an EVO transactions export. In EVO's portal, export Transactions as CSV"


@dataclass
class EvoFile:
    payments: List[dict] = field(default_factory=list)  # backend.payments input format
    errors: List[str] = field(default_factory=list)  # "row <n>: what's wrong"; those rows were left out
    rows: int = 0
    synthetic: bool = False  # True only if every row is marked as the synthetic demo export
    first_day: Optional[str] = None
    last_day: Optional[str] = None


def _kind(desc: str) -> str:
    d = desc.lower()
    return "refund" if "refund" in d else "sale" if "sale" in d else "other"


def _gbp(text: str) -> float:
    return abs(float(text.strip().replace("£", "").replace(",", "")))  # "£1,234.00" → 1234.0


def _row(r: dict) -> dict:
    day = datetime.strptime(r["Date"].strip(), "%d/%m/%Y").date().isoformat()
    fee = (r.get("MSCAmount") or "").strip()
    return {
        "source": "evo", "source_id": r["TransactionID"].strip(), "paid_at": day,
        "amount_gbp": _gbp(r["Amount"]), "fee_gbp": _gbp(fee) if fee else None,
        "currency": (r.get("Transaction currency") or "GBP").strip().upper(), "kind": _kind(r.get("ChargeDesc") or ""),
        "status": "successful",  # the export lists settled transactions only
        "card_product": {"debit": "debit", "credit": "credit"}.get((r.get("Product") or "").strip().lower()),
        "card_category": {"consumer": "consumer", "corporate": "corporate"}.get((r.get("CardType") or "").strip().lower()),
        "region": REGIONS.get((r.get("Region") or "").strip().lower()),
        "brand": (r.get("CardBrandDesc") or "").strip() or None, "entry_mode": None,
    }


def parse(data: bytes) -> EvoFile:
    """EVO transactions CSV (bytes) → payments. Raises ValueError if it isn't an EVO transactions export at all."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            raise ValueError(NOT_EVO) from None
    try:
        return _read(text)
    except csv.Error as e:  # e.g. a field over the csv module's size limit: not an EVO export
        raise ValueError(f"{NOT_EVO} ({e})") from None


def _read(text: str) -> EvoFile:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{NOT_EVO} (missing column(s): {', '.join(missing)})")
    out, marks = EvoFile(), []
    for n, r in enumerate(reader, 2):  # row 1 is the header
        out.rows += 1
        marks.append((r.get("Merchant Name") or "").strip().upper().endswith(SYNTHETIC_MARK))  # read, compared, dropped
        try:
            p = _row({k: r.get(k) or "" for k in KEEP})
        except (ValueError, KeyError) as e:
            out.errors.append(f"row {n}: {e}")
            continue
        out.payments.append(p)
    if not out.rows:
        raise ValueError("This EVO export has no payments in it. Check the date range you exported.")
    out.synthetic = all(marks)
    days = sorted(p["paid_at"] for p in out.payments)
    out.first_day, out.last_day = (days[0], days[-1]) if days else (None, None)
    return out


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Import an EVO transactions CSV (card data is never stored).")
    parser.add_argument("path")
    parser.add_argument("--dry-run", action="store_true", help="check the file and show totals; store nothing")
    args = parser.parse_args(argv)
    try:
        with open(args.path, "rb") as f:
            parsed = parse(f.read())
    except (OSError, ValueError) as e:
        raise SystemExit(f"Not imported: {e}")
    sales = [p for p in parsed.payments if p["kind"] == "sale"]
    print(f"{parsed.rows} rows, {len(sales)} sales £{sum(p['amount_gbp'] for p in sales):,.2f}, "
          f"{parsed.first_day} → {parsed.last_day}, {'SYNTHETIC' if parsed.synthetic else 'REAL'} data, {len(parsed.errors)} bad row(s)")
    for e in parsed.errors[:5]:
        print("  ", e)
    if args.dry_run:
        return
    from backend.payments import record_payments
    try:
        print(record_payments(parsed.payments, origin="EVO CSV (command line)", synthetic=parsed.synthetic).message)
    except ValueError as e:  # demo database refused, or the salt is missing/changed: nothing was stored
        raise SystemExit(f"Not imported: {e}")


if __name__ == "__main__":
    main()
