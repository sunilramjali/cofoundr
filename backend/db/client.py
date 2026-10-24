"""Shared MongoDB Atlas client.

Use `get_db()` everywhere; never create your own MongoClient. One client is
created per process and reused (it's thread-safe and pools connections), which
suits sync FastAPI `def` endpoints running in a thread pool.
"""
import os
from functools import lru_cache

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
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


def ping() -> bool:
    return get_client().admin.command("ping").get("ok") == 1.0


def ensure_indexes(db: Database) -> None:
    """Create the indexes the app relies on. Safe to run repeatedly."""
    db[schema.INVOICES].create_index([("business_id", ASCENDING), ("status", ASCENDING), ("due_date", ASCENDING)])
    db[schema.INVOICES].create_index([("business_id", ASCENDING), ("invoice_number", ASCENDING)], unique=True)
    db[schema.APPROVALS].create_index([("business_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
