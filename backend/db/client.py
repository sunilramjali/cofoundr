"""Shared MongoDB Atlas client.

Use `get_db()` everywhere; never create your own MongoClient. One client is
created per process and reused (it's thread-safe and pools connections), which
suits sync FastAPI `def` endpoints running in a thread pool.
"""
import os
from functools import lru_cache

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.database import Database

from backend.db import schema

load_dotenv()


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI is not set. Add it to .env (see .env.example).")
    return MongoClient(
        uri,
        appname="cofoundr",
        tz_aware=True,  # datetimes come back as timezone-aware UTC
        serverSelectionTimeoutMS=10_000,
    )


def get_db() -> Database:
    return get_client()[os.environ.get("MONGODB_DB", schema.DB_NAME)]


class RealDataRefused(ValueError):
    """Real business data must never land in the shared, synthetic demo database."""


def check_real_data_allowed(db: Database) -> None:
    """Call before storing anything real (synthetic=False). The demo database is read by /brief through a token in the
    URL, by both partners' local dev and by demo resets, which is only acceptable for synthetic data."""
    if db.name == schema.DB_NAME:
        raise RealDataRefused(f"Real data can't be stored in the shared demo database ({schema.DB_NAME}). "
                              "Set MONGODB_DB to your own database (e.g. gaia-real) and import again.")


def ping() -> bool:
    return get_client().admin.command("ping").get("ok") == 1.0


def ensure_indexes(db: Database) -> None:
    """Create the indexes the app relies on. Safe to run repeatedly (also migrates older data)."""
    invoices, approvals = db[schema.INVOICES], db[schema.APPROVALS]
    invoices.create_index([("business_id", ASCENDING), ("status", ASCENDING), ("due_date", ASCENDING)])

    # Invoice numbers are only unique per counterparty ("INV-001" from two suppliers is two invoices).
    if "business_id_1_invoice_number_1" in invoices.index_information():
        invoices.drop_index("business_id_1_invoice_number_1")
    for doc in invoices.find({"counterparty_key": {"$exists": False}}, {"counterparty": 1}):
        invoices.update_one({"_id": doc["_id"]}, {"$set": {"counterparty_key": schema.counterparty_key(doc.get("counterparty"))}})
    invoices.create_index([("business_id", ASCENDING), ("direction", ASCENDING), ("counterparty_key", ASCENDING),
                           ("invoice_number", ASCENDING)], unique=True, name="invoice_identity")

    approvals.create_index([("business_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
    # One pending action per invoice, even if two overdue checks run at the same moment
    approvals.create_index([("target_id", ASCENDING)], unique=True, name="one_pending_per_invoice",
                           partialFilterExpression={"status": "pending"})
    # "approve 5" must mean exactly one action
    approvals.create_index([("business_id", ASCENDING), ("ref", ASCENDING)], unique=True, name="unique_ref",
                           partialFilterExpression={"ref": {"$exists": True}})

    # Till data: SALES is a time-series collection (one doc per order line, bucketed by hour)
    if schema.SALES not in db.list_collection_names():
        try:
            db.create_collection(schema.SALES, timeseries={"timeField": "ts", "metaField": "meta", "granularity": "hours"})
        except NotImplementedError:  # mongomock in tests has no time-series support: a plain collection is fine there
            db.create_collection(schema.SALES)
    db[schema.SALES].create_index([("meta.business_id", ASCENDING), ("meta.sku", ASCENDING), ("ts", ASCENDING)])
    db[schema.SALES].create_index([("order_id", ASCENDING)])
    db[schema.ORDERS].create_index([("business_id", ASCENDING), ("placed_at", ASCENDING)])
    db[schema.STOCK_COUNTS].create_index([("business_id", ASCENDING), ("counted_at", ASCENDING)])
    db[schema.PAYMENTS].create_index([("business_id", ASCENDING), ("date", ASCENDING)])
    # Weather + events around the pitches (backend/conditions.py): read by date for the forecast
    db[schema.WEATHER].create_index([("business_id", ASCENDING), ("pitch_id", ASCENDING), ("date", ASCENDING)])
    db[schema.EVENTS].create_index([("business_id", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)])
    db[schema.EVENTS].create_index([("business_id", ASCENDING), ("pitch_id", ASCENDING), ("date", ASCENDING)])
    db[schema.TRANSPORT].create_index([("business_id", ASCENDING), ("state", ASCENDING), ("dates", ASCENDING)])


def next_refs(db: Database, business_id: str, n: int) -> list:
    """Atomically reserve n approval reference numbers (safe under concurrent overdue checks)."""
    if n <= 0:
        return []
    key = f"{business_id}:approval_ref"
    # First use on an existing database: start after the highest ref already in use
    last = db[schema.APPROVALS].find_one({"business_id": business_id, "ref": {"$exists": True}}, sort=[("ref", -1)])
    db[schema.COUNTERS].update_one({"_id": key}, {"$max": {"seq": last["ref"] if last else 0}}, upsert=True)
    seq = db[schema.COUNTERS].find_one_and_update({"_id": key}, {"$inc": {"seq": n}}, return_document=ReturnDocument.AFTER)["seq"]
    return list(range(seq - n + 1, seq + 1))
