"""Agent activity log: one line per action, shown in the dashboard."""
from datetime import datetime, timezone

from backend.db import schema
from backend.db.client import get_db

BUSINESS_ID = "gaia-coffee"


def log(kind: str, summary: str, actor: str = "agent", **detail) -> None:
    get_db()[schema.AGENT_LOG].insert_one({
        "business_id": BUSINESS_ID,
        "kind": kind,
        "summary": summary,
        "actor": actor,
        "detail": detail,
        "at": datetime.now(timezone.utc),
        "synthetic": True,
    })


def recent(limit: int = 15) -> list:
    return list(get_db()[schema.AGENT_LOG].find({"business_id": BUSINESS_ID}).sort("at", -1).limit(limit))
