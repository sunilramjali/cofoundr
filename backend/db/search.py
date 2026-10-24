"""Invoice search on MongoDB Atlas (MVP2): keyword, meaning, and both at once.

Atlas features used (each doing the job it's best at):
- Atlas Search ("invoices_text"): typo-tolerant keyword search; "diary" still finds Eastside Dairy, "EDC-7781" finds that invoice.
- Automated Embedding + Vector Search ("invoices_semantic"): Atlas embeds `search_text` itself with a Voyage AI model on
  every insert/update (no embedding code here) so we can search by meaning: "milk supplier" finds Meadowfield Dairy.
- Hybrid Search ($rankFusion): one search box that merges both rankings.

Without Atlas Search (tests on mongomock, an index missing or still building) every function falls back to a plain
word match and says so (`mode = "basic"`, logged in agent_log at most once an hour), so the dashboard and Saint never
break and the failure is never silent.

Usage (from the repo root):
    python -m backend.db.search --setup              # backfill search_text + create/update the Atlas indexes
    python -m backend.db.search "milk we owe"        # hybrid search
    python -m backend.db.search --similar MF-2087    # similar invoices + price check
"""
import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from backend.db import schema

BUSINESS_ID = "gaia-coffee"
TEXT_INDEX = "invoices_text"
SEMANTIC_INDEX = "invoices_semantic"
EMBEDDING_MODEL = "voyage-4"  # Voyage AI model Atlas uses for Automated Embedding
MIN_ITEM_MATCH = 0.5  # share of words two line items must have in common to compare their prices
# A hit found only by meaning is shown if it's close enough (Voyage cosine score, measured on our invoices:
# real matches 0.70-0.83, unrelated ones 0.58-0.68) and not far behind the best meaning match.
MIN_MEANING_SCORE, MAX_MEANING_GAP = 0.70, 0.06
FUZZY_MIN_LEN = 5  # shorter words must match exactly
MAX_QUERY_LEN = 200  # a pasted email is not a search
RISE_PCT = 2.0  # price differences smaller than this are noise, not news
STOPWORDS = {"a", "an", "and", "of", "the", "for", "per", "x"}
# Pack sizes: "2L", "1 litre", "8oz", "(1000)". Two items are only comparable when their sizes match exactly.
SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|cl|litres?|liters?|l|kg|g|oz|lb)?\b", re.I)
UNITS = {"litre": "l", "litres": "l", "liter": "l", "liters": "l"}

INDEXES = {
    TEXT_INDEX: ("search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "invoice_number": {"type": "string"},
        "counterparty": {"type": "string", "analyzer": "lucene.english"},
        "line_items": {"type": "document", "fields": {"description": {"type": "string", "analyzer": "lucene.english"}}},
    }}}),
    SEMANTIC_INDEX: ("vectorSearch", {"fields": [
        {"type": "autoEmbed", "modality": "text", "path": "search_text", "model": EMBEDDING_MODEL},
        {"type": "filter", "path": "business_id"},
        {"type": "filter", "path": "direction"},
    ]}),
}

FIELDS = {"invoice_number": 1, "counterparty": 1, "direction": 1, "amount": 1, "currency": 1, "issue_date": 1,
          "due_date": 1, "status": 1, "needs_review": 1, "line_items": 1, "search_text": 1, "counterparty_key": 1,
          "created_at": 1}


@dataclass
class SearchResult:
    hits: List[dict]  # invoice fields above + "score" + "matched_by" (["keyword"], ["meaning"] or both)
    mode: str  # "hybrid" = Atlas Search + Vector Search, "basic" = plain word match (Atlas Search unavailable)
    error: Optional[str] = None  # why it fell back, for logs (never shown to the owner)


@dataclass
class PriceCheck:
    invoice_number: str
    # One per (line item, kind) with a comparable price on an EARLIER, trusted invoice in the same currency:
    # kind "same_supplier"  = this supplier's own last price (a rise here is price creep)
    # kind "other_supplier" = the closest match from another supplier (a lower price there = cheaper elsewhere)
    comparisons: List[dict] = field(default_factory=list)
    mode: str = "hybrid"
    skipped: Optional[str] = None  # why nothing was compared, e.g. the invoice itself still needs the owner's check

    @property
    def rises(self) -> List[dict]:
        return [c for c in self.comparisons if c["kind"] == "same_supplier" and c["change_pct"] >= RISE_PCT]

    @property
    def cheaper_elsewhere(self) -> List[dict]:
        return [c for c in self.comparisons if c["kind"] == "other_supplier" and c["change_pct"] >= RISE_PCT]


def ensure_search_indexes(db: Database) -> Dict[str, str]:
    """Backfill `search_text` and create (or update) the Atlas Search indexes. Safe to re-run.
    Returns {index name: status}; {} when the server has no Atlas Search (e.g. mongomock)."""
    invoices = db[schema.INVOICES]
    for doc in invoices.find({"search_text": {"$exists": False}}):
        invoices.update_one({"_id": doc["_id"]}, {"$set": {"search_text": schema.invoice_search_text(doc)}})
    try:
        existing = {i["name"]: i for i in invoices.list_search_indexes()}
    except (OperationFailure, NotImplementedError, AttributeError, TypeError):  # TypeError: mongomock has no such method
        return {}
    for name, (kind, definition) in INDEXES.items():
        if name not in existing:
            invoices.create_search_index(SearchIndexModel(definition, name=name, type=kind))
        elif existing[name].get("latestDefinition") != definition:
            invoices.update_search_index(name, definition)
    return {i["name"]: i.get("status", "?") for i in invoices.list_search_indexes() if i["name"] in INDEXES}


def search_invoices(db: Database, query: str, business_id: str = BUSINESS_ID, limit: int = 8) -> SearchResult:
    """Hybrid search: keyword (typo-tolerant) and meaning, merged with $rankFusion."""
    query = (query or "").strip()[:MAX_QUERY_LEN]
    if not re.search(r"\w", query):
        return SearchResult([], "hybrid")
    pipelines = {
        "keyword": [{"$search": {"index": TEXT_INDEX, "compound": {
            "filter": [{"equals": {"path": "business_id", "value": business_id}}],
            "should": _keyword_clauses(query),
            "minimumShouldMatch": 1}}},
            {"$limit": limit}],
        "meaning": [{"$vectorSearch": {"index": SEMANTIC_INDEX, "path": "search_text", "query": {"text": query},
                                       "filter": {"business_id": business_id}, "numCandidates": 50, "limit": limit}}],
    }
    try:
        docs = list(db[schema.INVOICES].aggregate([
            {"$rankFusion": {"input": {"pipelines": pipelines}, "scoreDetails": True}},
            {"$project": {**FIELDS, "score": {"$meta": "score"}, "details": {"$meta": "scoreDetails"}}},
        ]))
    except (OperationFailure, NotImplementedError) as e:
        return _fallback(db, _basic_search(db, query, business_id, limit), str(e)[:200])
    hits = _relevant(docs)[:limit]
    if not hits:  # Atlas answers a missing or still-building index with no results, not an error: double-check
        basic = _basic_search(db, query, business_id, limit)
        if basic:
            return _fallback(db, basic, "Atlas Search returned nothing but a plain match did (index missing or building?)")
    return SearchResult(hits, "hybrid")


def similar_invoices(db: Database, invoice: dict, limit: int = 5) -> SearchResult:
    """Earlier invoices most like this one by meaning (same direction): another dairy's milk bill for a milk bill."""
    text = invoice.get("search_text") or schema.invoice_search_text(invoice)
    business_id = invoice.get("business_id", BUSINESS_ID)
    try:
        docs = list(db[schema.INVOICES].aggregate([
            {"$vectorSearch": {"index": SEMANTIC_INDEX, "path": "search_text", "query": {"text": text},
                               "filter": {"business_id": business_id, "direction": invoice.get("direction")},
                               "numCandidates": 50, "limit": limit + 1}},
            {"$match": {"_id": {"$ne": invoice.get("_id")}}},
            {"$limit": limit},
            {"$project": {**FIELDS, "score": {"$meta": "vectorSearchScore"}}},
        ]))
    except (OperationFailure, NotImplementedError) as e:
        return _fallback(db, _basic_similar(db, invoice, limit), str(e)[:200])
    for d in docs:
        d["matched_by"] = ["meaning"]
    return SearchResult(docs, "hybrid")


def price_check(db: Database, invoice: dict) -> PriceCheck:
    """Compare each line item's unit price with the same item (same pack size) on earlier invoices:
    this supplier's last price ("same_supplier") and the closest other supplier's ("other_supplier").
    "Whole Milk (2L) £1.90 vs £1.85 at Eastside Dairy Co (EDC-7781): +2.7%"."""
    check = PriceCheck(invoice.get("invoice_number", ""))
    if invoice.get("needs_review"):
        check.skipped = "the invoice still needs the owner's check, so its prices aren't trusted yet"
        return check
    similar = similar_invoices(db, invoice, limit=8)
    check.mode = similar.mode
    key = invoice.get("counterparty_key") or schema.counterparty_key(invoice.get("counterparty"))
    # The supplier's own earlier bills matter most, so fetch them directly rather than hoping they rank in the top 8
    own = list(db[schema.INVOICES].find({"business_id": invoice.get("business_id", BUSINESS_ID), "counterparty_key": key,
                                         "direction": invoice.get("direction"), "_id": {"$ne": invoice.get("_id")}}, FIELDS))
    seen, candidates = set(), []
    for other in similar.hits + own:  # similar first: earlier position = more similar, wins ties
        if other.get("_id") in seen or not _comparable(invoice, other):
            continue
        seen.add(other.get("_id"))
        candidates.append(other)

    for item in invoice.get("line_items") or []:
        if not _positive(item.get("unit_price")):
            continue
        for kind in ("same_supplier", "other_supplier"):
            pool = [o for o in candidates if (_key(o) == key) == (kind == "same_supplier")]
            match = _best_match(item, pool, latest_first=kind == "same_supplier")
            if match:
                other, li = match
                check.comparisons.append({
                    "kind": kind, "item": item["description"], "unit_price": item["unit_price"],
                    "compared_to": li["description"], "other_price": li["unit_price"],
                    "counterparty": other.get("counterparty"), "invoice_number": other.get("invoice_number"),
                    "change_pct": round((item["unit_price"] - li["unit_price"]) / li["unit_price"] * 100, 1),
                })
    return check


# ---------------------------------------------------------------- helpers

def _words(text: Optional[str]) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS}


def _sizes(text: Optional[str]) -> set:
    """{"2l"} for "Whole Milk (2L)" and "Whole milk, 2 litres"; {"8oz", "1000"} for "8oz cups (1000)"."""
    return {f"{float(n):g}{UNITS.get(u.lower(), u.lower()) if u else ''}" for n, u in SIZE.findall(text or "")}


def _positive(price) -> bool:
    return isinstance(price, (int, float)) and price > 0


def _key(doc: dict) -> str:
    return doc.get("counterparty_key") or schema.counterparty_key(doc.get("counterparty"))


def _when(doc: dict) -> datetime:
    """When the invoice was issued, or received if the date couldn't be read; always timezone-aware."""
    when = doc.get("issue_date") or doc.get("created_at") or datetime.now(timezone.utc)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _comparable(invoice: dict, other: dict) -> bool:
    """Only earlier, trusted invoices in the same currency are a fair price reference."""
    return (not other.get("needs_review")
            and (other.get("currency") or "GBP") == (invoice.get("currency") or "GBP")
            and _when(other) < _when(invoice))


def _best_match(item: dict, pool: List[dict], latest_first: bool):
    """The closest line item with the same pack size: highest word overlap, then (for the supplier's own bills)
    the most recent invoice, else the most similar one. None when nothing is close enough."""
    best, best_rank = None, None
    for position, other in enumerate(pool):
        for li in other.get("line_items") or []:
            overlap = _overlap(item.get("description"), li.get("description"))
            if overlap < MIN_ITEM_MATCH or not _positive(li.get("unit_price")) \
                    or _sizes(item.get("description")) != _sizes(li.get("description")):
                continue
            rank = (overlap, _when(other).timestamp() if latest_first else -position)
            if best_rank is None or rank > best_rank:
                best, best_rank = (other, li), rank
    return best


def _fallback(db: Database, hits: List[dict], error: str) -> SearchResult:
    """Basic-mode result, and one `search_fallback` line in agent_log per hour so a broken index is never silent."""
    log = db[schema.AGENT_LOG]
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    if not log.find_one({"business_id": BUSINESS_ID, "kind": "search_fallback", "at": {"$gte": since}}):
        log.insert_one({"business_id": BUSINESS_ID, "kind": "search_fallback", "actor": "agent", "synthetic": True,
                        "summary": "Atlas Search unavailable: using a plain word match for now", "detail": {"error": error},
                        "at": datetime.now(timezone.utc)})
    return SearchResult(hits, "basic", error)


def _overlap(a: Optional[str], b: Optional[str]) -> float:
    """Share of words in common (Jaccard), ignoring sizes (checked separately by _sizes, and a shared "1l" must not
    make "Whole milk, 1L" look like "Oat milk, 1L"): "Oat Barista Milk (1L)" vs "Oat milk, 1L" = 0.67."""
    wa, wb = {w for w in _words(a) if not w[0].isdigit()}, {w for w in _words(b) if not w[0].isdigit()}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _keyword_clauses(query: str) -> List[dict]:
    """Exact words, plus typo tolerance for words of 5+ letters only ("diary" → Dairy, but "milk" must not match "Mile")."""
    fields = ["counterparty", "line_items.description"]
    clauses = [{"text": {"query": query, "path": fields, "score": {"boost": {"value": 2}}}},
               {"text": {"query": query, "path": "invoice_number", "score": {"boost": {"value": 3}}}}]
    long_words = [w for w in re.findall(r"[A-Za-z]+", query) if len(w) >= FUZZY_MIN_LEN]
    if long_words:
        clauses.append({"text": {"query": " ".join(long_words), "path": fields, "fuzzy": {"maxEdits": 1}}})
    return clauses


def _relevant(docs: List[dict]) -> List[dict]:
    """Set "matched_by" from $rankFusion's scoreDetails and drop weak meaning-only hits
    (vector search always returns its nearest neighbours, even for "EDC-7781")."""
    for d in docs:
        found = {p["inputPipelineName"]: p.get("value", 0.0) for p in (d.pop("details", None) or {}).get("details", [])
                 if p.get("rank")}
        d["matched_by"] = [p for p in ("keyword", "meaning") if p in found]
        d["meaning_score"] = found.get("meaning")
    best = max([d["meaning_score"] or 0.0 for d in docs] + [0.0])
    return [d for d in docs if "keyword" in d["matched_by"]
            or (d["meaning_score"] or 0.0) >= max(MIN_MEANING_SCORE, best - MAX_MEANING_GAP)]


def _basic_search(db: Database, query: str, business_id: str, limit: int) -> List[dict]:
    words = _words(query)
    hits = []
    for doc in db[schema.INVOICES].find({"business_id": business_id}, FIELDS):
        text = _words(doc.get("search_text") or schema.invoice_search_text(doc))
        score = len(words & text)
        if score:
            hits.append({**doc, "score": float(score), "matched_by": ["keyword"]})
    return sorted(hits, key=lambda d: -d["score"])[:limit]


def _basic_similar(db: Database, invoice: dict, limit: int) -> List[dict]:
    mine = _words(invoice.get("search_text") or schema.invoice_search_text(invoice))
    hits = []
    for doc in db[schema.INVOICES].find({"business_id": invoice.get("business_id", BUSINESS_ID),
                                          "direction": invoice.get("direction"), "_id": {"$ne": invoice.get("_id")}}, FIELDS):
        score = _overlap(" ".join(mine), doc.get("search_text") or schema.invoice_search_text(doc))
        if score:
            hits.append({**doc, "score": round(score, 3), "matched_by": ["meaning"]})
    return sorted(hits, key=lambda d: -d["score"])[:limit]


def main() -> None:
    from backend.db.client import get_db

    parser = argparse.ArgumentParser(description="Search invoices on Atlas (keyword + meaning).")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--setup", action="store_true", help="backfill search_text and create/update the indexes")
    parser.add_argument("--similar", metavar="INVOICE_NUMBER", help="similar invoices + price check")
    args = parser.parse_args()
    db = get_db()
    if args.setup:
        print(ensure_search_indexes(db))
    if args.query:
        result = search_invoices(db, args.query)
        print(f"mode: {result.mode}" + (f" ({result.error})" if result.error else ""))
        for h in result.hits:
            print(f"  {h['score']:.4f}  {h['invoice_number']:<14} {h['counterparty']:<30} £{h['amount']:>8.2f}  {'+'.join(h['matched_by'])}")
    if args.similar:
        invoice = db[schema.INVOICES].find_one({"business_id": BUSINESS_ID, "invoice_number": args.similar})
        if not invoice:
            raise SystemExit(f"No invoice {args.similar}")
        for h in similar_invoices(db, invoice).hits:
            print(f"  similar {h['score']:.3f}  {h['invoice_number']:<14} {h['counterparty']}")
        check = price_check(db, invoice)
        if check.skipped:
            print(f"  price check skipped: {check.skipped}")
        for c in check.comparisons:
            print(f"  [{c['kind']}] {c['item']}: £{c['unit_price']:.2f} vs £{c['other_price']:.2f} at {c['counterparty']} "
                  f"({c['invoice_number']}): {c['change_pct']:+.1f}%")


if __name__ == "__main__":
    main()
