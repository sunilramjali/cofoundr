"""Seed MongoDB Atlas with SYNTHETIC GAIA Coffee demo data (MVP1).

Usage (from the repo root):
    python -m data.seed --dry-run   # print what would be inserted, no DB needed
    python -m data.seed             # insert (refuses if data already exists)
    python -m data.seed --reset     # delete existing synthetic demo data, then insert

Dates are relative to today, so overdue invoices stay overdue whenever you run it.
All names and emails are fictional; every document has synthetic=True.
"""
import argparse
from datetime import datetime, timedelta, timezone

from backend.db import schema

BUSINESS_ID = "gaia-coffee"
SEEDED = [schema.BUSINESSES, schema.INVOICES, schema.APPROVALS, schema.AGENT_LOG]


def _today() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def build_business(now: datetime) -> dict:
    return {
        "_id": BUSINESS_ID,
        "name": "GAIA Coffee",
        "business_type": "Mobile coffee truck",
        "location": "East London",
        "currency": "GBP",
        "synthetic": True,
        "created_at": now,
    }


# (number, direction, counterparty, email, issued_days_ago, due_in_days, paid, reminders_sent, line_items)
# due_in_days < 0 means the due date has passed.
_INVOICES = [
    # Receivables: customers owe GAIA. Three overdue at escalating levels.
    ("GAIA-1041", "receivable", "Hackney Hub Coworking", "accounts@hackneyhub.example.com", 33, -3, False, 0,
     [("Office coffee cart, 2 days", 2, 180.00), ("Pastry platter", 2, 45.00)]),
    ("GAIA-1038", "receivable", "Victoria Park Wedding Co", "hello@vpweddings.example.com", 39, -9, False, 1,
     [("Wedding coffee bar, 4 hours", 1, 420.00), ("Oat milk upgrade", 1, 35.00)]),
    ("GAIA-1032", "receivable", "Shoreditch Design Studio", "finance@sdstudio.example.com", 51, -21, False, 2,
     [("Team breakfast catering", 3, 96.00)]),
    ("GAIA-1045", "receivable", "Broadway Market Traders Assoc.", "admin@bmtraders.example.com", 6, 24, False, 0,
     [("Market day pitch coffee", 1, 260.00)]),
    ("GAIA-1046", "receivable", "Mile End Climbing Wall", "office@meclimb.example.com", 2, 28, False, 0,
     [("Event coffee, 120 cups", 120, 2.50)]),
    ("GAIA-1029", "receivable", "Hackney Hub Coworking", "accounts@hackneyhub.example.com", 62, -32, True, 0,
     [("Office coffee cart, 1 day", 1, 180.00)]),
    # Payables: GAIA owes suppliers.
    ("EDC-7781", "payable", "Eastside Dairy Co", "billing@eastsidedairy.example.com", 12, 2, False, 0,
     [("Whole milk, 2L", 40, 1.85), ("Oat milk, 1L", 36, 2.10)]),
    ("BLR-2209", "payable", "Bow Lane Roasters", "orders@bowlaneroasters.example.com", 20, 10, False, 0,
     [("House espresso blend, 1kg", 12, 19.50), ("Decaf, 1kg", 2, 21.00)]),
    ("CLS-0457", "payable", "Cup & Lid Supplies", "accounts@cupandlid.example.com", 35, -5, False, 0,
     [("8oz compostable cups (1000)", 2, 64.00), ("Lids (1000)", 2, 28.00)]),
    ("MEB-3310", "payable", "Mile End Bakery", "hello@mileendbakery.example.com", 25, -11, True, 0,
     [("Croissants", 60, 1.20), ("Banana bread slices", 40, 1.50)]),
]


def build_invoices(today: datetime, now: datetime) -> list:
    docs = []
    for number, direction, party, email, issued_ago, due_in, paid, reminders, items in _INVOICES:
        due_date = today + timedelta(days=due_in)
        line_items = [{"description": d, "qty": float(q), "unit_price": p} for d, q, p in items]
        if paid:
            status, paid_at = "paid", due_date - timedelta(days=1)
        else:
            status, paid_at = ("overdue" if due_date < today else "open"), None
        docs.append({
            "business_id": BUSINESS_ID,
            "invoice_number": number,
            "direction": direction,
            "counterparty": party,
            "counterparty_email": email,
            "amount": round(sum(li["qty"] * li["unit_price"] for li in line_items), 2),
            "currency": "GBP",
            "issue_date": today - timedelta(days=issued_ago),
            "due_date": due_date,
            "line_items": line_items,
            "status": status,
            "paid_at": paid_at,
            "source": "manual",
            "extraction_confidence": 1.0,
            "needs_review": False,
            "reminders_sent": reminders,
            "synthetic": True,
            "created_at": now,
        })
    return docs


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic GAIA Coffee demo data.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print documents, don't touch the DB")
    group.add_argument("--reset", action="store_true", help="delete existing synthetic data first")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    business = build_business(now)
    invoices = build_invoices(_today(), now)

    if args.dry_run:
        print(f"[dry run] 1 business: {business['name']} ({business['_id']})")
        for inv in invoices:
            print(f"  {inv['invoice_number']:<10} {inv['direction']:<10} {inv['status']:<8} "
                  f"£{inv['amount']:>8.2f}  due {inv['due_date']:%a %d %b}  {inv['counterparty']}")
        print("[dry run] approvals: 0 (created later by the overdue check)")
        return

    from backend.db.client import ensure_indexes, get_db  # only needs MONGODB_URI when writing

    db = get_db()
    if args.reset:
        for name in SEEDED:
            deleted = db[name].delete_many({"synthetic": True}).deleted_count
            print(f"reset: removed {deleted} synthetic docs from {name}")
        cleared = db[schema.CURSORS].delete_many({"_id": {"$regex": f"^{BUSINESS_ID}:"}}).deleted_count
        print(f"reset: cleared {cleared} /updates cursor(s)")
    elif db[schema.BUSINESSES].find_one({"_id": BUSINESS_ID}):
        raise SystemExit("Demo data already exists. Re-run with --reset to replace it.")

    ensure_indexes(db)
    db[schema.BUSINESSES].insert_one(business)
    db[schema.INVOICES].insert_many(invoices)
    # Make sure the approvals collection exists (empty) so the dashboard can query it.
    if schema.APPROVALS not in db.list_collection_names():
        db.create_collection(schema.APPROVALS)

    overdue = sum(1 for i in invoices if i["status"] == "overdue")
    print(f"Seeded {db.name}: 1 business, {len(invoices)} invoices ({overdue} overdue), approvals ready.")


if __name__ == "__main__":
    main()
