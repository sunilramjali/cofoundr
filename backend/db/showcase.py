"""Under the hood (MVP2): every MongoDB Atlas feature CoFoundr uses, run live, with the exact query it sent.

For the "Under the hood" page (/mongodb) and the demo video. Each section calls the same function the product uses
(nothing staged for the page), through a recorder that keeps every aggregation pipeline sent to Atlas, so what's shown
is what ran. Read-only: nothing here writes, except search's own `search_fallback` log line when an index is down.

Usage (from the repo root):
    python -m backend.db.showcase      # print every section: feature, result, time, mode
"""
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List

from bson import ObjectId
from pymongo.database import Database

from backend.db import schema, search

BUSINESS_ID = "gaia-coffee"
CACHE_SECONDS = 60  # one page view runs ~15 Atlas queries: a refresh within a minute shows the same run
MAX_PIPELINE_CHARS = 2500
_cache: dict = {"at": 0.0, "sections": None}


class _Recorder:
    """Wraps a Database: every `aggregate` on any collection is recorded as (collection, pipeline), then sent as is."""
    def __init__(self, db: Database):
        self._db, self.calls = db, []

    def __getitem__(self, name):
        return _RecordedCollection(self._db[name], name, self.calls)

    def __getattr__(self, attr):
        return getattr(self._db, attr)


class _RecordedCollection:
    def __init__(self, coll, name: str, calls: list):
        self._coll, self._name, self._calls = coll, name, calls

    def aggregate(self, pipeline, *args, **kwargs):
        self._calls.append((self._name, pipeline))
        return self._coll.aggregate(pipeline, *args, **kwargs)

    def __getattr__(self, attr):
        return getattr(self._coll, attr)


def _pipeline_text(collection: str, pipeline: list) -> str:
    text = f"db.{collection}.aggregate(" + json.dumps(pipeline, indent=1, default=str, ensure_ascii=False) + ")"
    return text if len(text) <= MAX_PIPELINE_CHARS else text[:MAX_PIPELINE_CHARS] + "\n  …"


def _section(db: Database, feature: str, title: str, why: str, work: Callable) -> dict:
    """Run one live demo: {"feature", "title", "why", "rows", "mode", "ms", "pipelines", "error"}."""
    rec = _Recorder(db)
    start = time.perf_counter()
    try:
        rows, mode = work(rec)
        error = None
    except Exception as e:  # noqa: BLE001 (shown on the page: one broken section never hides the others)
        rows, mode, error = [], "unavailable", f"{type(e).__name__}: {str(e)[:160]}"
    return {"feature": feature, "title": title, "why": why, "rows": rows, "mode": mode, "error": error,
            "ms": round((time.perf_counter() - start) * 1000), "pipelines": [_pipeline_text(c, p) for c, p in rec.calls]}


def _hybrid(db) -> tuple:
    r = search.search_invoices(db, "milk supplier")
    return [f"{h['counterparty']} · {h['invoice_number']} · £{h['amount']:.2f} · found by {' + '.join(h['matched_by'])}"
            for h in r.hits[:5]], r.mode


def _typo(db) -> tuple:
    r = search.find_products(db, "flat whte")
    return [f"“flat whte” → {h['name']}" for h in r.hits[:3]], r.mode


def _autocomplete(db) -> tuple:
    r = search.suggest(db, "eas")
    return [f"“eas” → {h['name']}" for h in r.hits], r.mode


def _facets(db) -> tuple:
    f = search.invoice_facets(db)
    return ([f"{f['total']} invoices"] + [f"{name}: {n}" for name, n in f["counterparty"][:4]]
            + [f"status {s}: {n}" for s, n in f["status"]]), f["mode"]


def _similar(db) -> tuple:
    inv = db[schema.INVOICES].find_one({"business_id": BUSINESS_ID, "direction": "payable", "needs_review": {"$ne": True}},
                                       sort=[("issue_date", -1)])
    if not inv:
        return ["no supplier bill yet"], "basic"
    similar = search.similar_invoices(db, inv, limit=4)
    rows = [f"{inv['counterparty']} {inv['invoice_number']} is most like:"]
    rows += [f"{h['counterparty']} · {h['invoice_number']} (similarity {h['score']:.2f})" for h in similar.hits]
    rows += [f"Price check: {line}" for line in search.price_check(db, inv).lines()[:2]]
    return rows, similar.mode


def _memory(db) -> tuple:
    from backend import memory

    a = db[schema.APPROVALS].find_one({"business_id": BUSINESS_ID, "status": "pending", "action_type": "invoice_reminder"},
                                      sort=[("ref", 1)])
    inv = db[schema.INVOICES].find_one({"_id": ObjectId(a["target_id"])}) if a else \
        db[schema.INVOICES].find_one({"business_id": BUSINESS_ID, "direction": "receivable", "status": {"$ne": "paid"}})
    if not inv:
        return ["no open customer invoice to recall for"], "basic"
    level = a.get("escalation_level") if a else min(inv.get("reminders_sent", 0), 2)
    how: dict = {}
    found = memory.recall(db, memory.case(inv, "invoice_reminder", a.get("days_overdue") if a else None, level), details=how)
    total = db[schema.MEMORIES].count_documents({"business_id": BUSINESS_ID})
    rows = [f"Before chasing {inv['counterparty']}, I recalled ({total} decision{'s' if total != 1 else ''} remembered):"]
    rows += [("This customer: " if m["same_counterparty"] else f"Similar case ({m['score']:.2f}): ") + m["line"] for m in found] \
        or ["nothing relevant yet: decide a few approvals (with a reason) and they show up here"]
    return rows, {"hybrid": "atlas"}.get(how.get("mode"), how.get("mode") or "direct")


def _news(db) -> tuple:
    r = search.news_feed(db, limit=4)
    return [f"{h['headline'][:70]}" + (f" · {h['distance_km']:.1f} km" if h.get("distance_km") is not None else "")
            for h in r.hits], r.mode


def _timeseries(db) -> tuple:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    days = list(db[schema.SALES].aggregate([
        {"$match": {"meta.business_id": BUSINESS_ID, "ts": {"$gte": since}}},
        {"$group": {"_id": {"$dateTrunc": {"date": "$ts", "unit": "day", "timezone": "Europe/London"}},
                    "takings": {"$sum": "$line_total"}, "lines": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    info = next(iter(db.list_collections(filter={"name": schema.SALES})), {})
    ts = (info.get("options") or {}).get("timeseries")
    rows = [f"time-series collection: timeField “{ts['timeField']}”, metaField “{ts['metaField']}”, "
            f"bucketed by {ts.get('granularity', '?')}" if ts else "plain collection here (no time-series support)"]
    rows += [f"{d['_id']:%a %d %b}: £{d['takings']:,.2f} from {d['lines']} till lines" for d in days]
    return rows, "atlas" if ts else "basic"


def indexes(db: Database) -> List[dict]:
    """Every Atlas Search / Vector Search index CoFoundr defines: name, collection, kind, status, embedding model."""
    out = []
    for name, (coll, kind, definition) in search.INDEXES.items():
        try:
            live = next((i for i in db[coll].list_search_indexes() if i["name"] == name), None)
            status = live.get("status", "?") if live else "missing"
        except Exception:  # noqa: BLE001 (no Atlas Search here, e.g. a local test database)
            status = "unavailable"
        models = [f["model"] for f in definition.get("fields", []) if f.get("type") == "autoEmbed"]
        out.append({"name": name, "collection": coll, "kind": "Vector Search" if kind == "vectorSearch" else "Atlas Search",
                    "status": status, "embedding": models[0] if models else None})
    return out


SECTIONS = [
    ("Hybrid Search", "Words and meaning, merged",
     "One search box: “milk supplier” finds the dairy by meaning and by keyword, merged by $rankFusion.", _hybrid),
    ("Vector Search + Automated Embedding", "Similar invoices and a price check",
     "Atlas embeds every invoice itself (Voyage): a new bill is compared with the most similar earlier ones, so price creep shows up.", _similar),
    ("Vector Search as memory", "What I remember about your decisions",
     "Your past yes/no and your reasons are recalled before I draft, so you never explain the same thing twice.", _memory),
    ("Atlas Search", "Typo-tolerant names",
     "“What if I put flat whte up 20p?” still finds Flat White: fuzzy matching on the menu.", _typo),
    ("Atlas Search autocomplete", "Suggestions as you type", "“eas” → Eastside Dairy Co, from an edgeGram index.", _autocomplete),
    ("Atlas Search facets ($searchMeta)", "Counts in one call", "Invoices per supplier and status for the Finances chips.", _facets),
    ("Atlas Search: near (dates + geo)", "Local news ranked by soon and close",
     "Events and news scored by how soon they start and how close they are to the pitch.", _news),
    ("Time-series collection", "Takings by day from every till line",
     "Each till line is stored in a time-series collection: a week of takings in one aggregation.", _timeseries),
]


def run(db: Database, fresh: bool = False) -> List[dict]:
    if not fresh and _cache["sections"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["sections"]
    sections = [_section(db, feature, title, why, work) for feature, title, why, work in SECTIONS]
    _cache.update(at=time.monotonic(), sections=sections)
    return sections


def main() -> None:
    from backend.db.client import get_db

    db = get_db()
    for s in run(db, fresh=True):
        print(f"\n▶ {s['feature']}: {s['title']}  [{s['mode']}, {s['ms']} ms, {len(s['pipelines'])} quer{'y' if len(s['pipelines']) == 1 else 'ies'}]")
        for row in s["rows"]:
            print(f"   {row}")
        if s["error"]:
            print(f"   ✗ {s['error']}")
    print("\nIndexes:")
    for i in indexes(db):
        print(f"   {i['name']:<20} {i['collection']:<12} {i['kind']:<13} {i['status']}" + (f"  autoEmbed {i['embedding']}" if i["embedding"] else ""))


if __name__ == "__main__":
    main()
