"""Invoice search on MongoDB Atlas (MVP2): keyword, meaning, and both at once.

Atlas features used (each doing the job it's best at):
- Atlas Search ("invoices_text"): typo-tolerant keyword search; "diary" still finds Eastside Dairy, "EDC-7781" finds that invoice.
- Automated Embedding + Vector Search ("invoices_semantic"): Atlas embeds `search_text` itself with a Voyage AI model on
  every insert/update (no embedding code here) so we can search by meaning: "milk supplier" finds Meadowfield Dairy.
- Hybrid Search ($rankFusion): one search box that merges both rankings.

Without Atlas Search (tests on mongomock, or an index still building) every function falls back to a plain word
match and says so (`mode = "basic"`), so the dashboard and Saint never break.

Usage (from the repo root):
    python -m backend.db.search --setup              # backfill search_text + create/update the Atlas indexes
    python -m backend.db.search "milk we owe"        # hybrid search
    python -m backend.db.search --similar MF-2087    # similar invoices + price check
"""
import argparse
import re
from dataclasses import dataclass, field
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
STOPWORDS = {"a", "an", "and", "of", "the", "for", "per", "x"}

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
          "due_date": 1, "status": 1, "needs_review": 1, "line_items": 1, "search_text": 1}


@dataclass
class SearchResult:
    hits: List[dict]  # invoice fields above + "score" + "matched_by" (["keyword"], ["meaning"] or both)
    mode: str  # "hybrid" = Atlas Search + Vector Search, "basic" = plain word match (Atlas Search unavailable)
    error: Optional[str] = None  # why it fell back, for logs (never shown to the owner)


@dataclass
class PriceCheck:
    invoice_number: str
    comparisons: List[dict] = field(default_factory=list)  # one per line item with a comparable earlier price
    mode: str = "hybrid"

    @property
    def rises(self) -> List[dict]:
        return [c for c in self.comparisons if c["change_pct"] > 0]


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
    query = (query or "").strip()
    if not query:
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
        return SearchResult(_basic_search(db, query, business_id, limit), "basic", str(e)[:200])
    return SearchResult(_relevant(docs)[:limit], "hybrid")


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
        return SearchResult(_basic_similar(db, invoice, limit), "basic", str(e)[:200])
    for d in docs:
        d["matched_by"] = ["meaning"]
    return SearchResult(docs, "hybrid")


def price_check(db: Database, invoice: dict) -> PriceCheck:
    """Compare each line item's unit price with the closest matching item on similar invoices.
    "Whole Milk (2L) £1.90 vs £1.85 at Eastside Dairy Co (EDC-7781): +2.7%"."""
    similar = similar_invoices(db, invoice, limit=8)
    check = PriceCheck(invoice.get("invoice_number", ""), mode=similar.mode)
    for item in invoice.get("line_items") or []:
        best, best_overlap = None, MIN_ITEM_MATCH
        for other in similar.hits:
            for li in other.get("line_items") or []:
                overlap = _overlap(item.get("description"), li.get("description"))
                if overlap >= best_overlap and li.get("unit_price"):
                    best, best_overlap = (other, li), overlap
        if best and item.get("unit_price") is not None:
            other, li = best
            check.comparisons.append({
                "item": item["description"], "unit_price": item["unit_price"],
                "compared_to": li["description"], "other_price": li["unit_price"],
                "counterparty": other.get("counterparty"), "invoice_number": other.get("invoice_number"),
                "change_pct": round((item["unit_price"] - li["unit_price"]) / li["unit_price"] * 100, 1),
            })
    return check


# ---------------------------------------------------------------- helpers

def _words(text: Optional[str]) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS}


def _overlap(a: Optional[str], b: Optional[str]) -> float:
    """Share of words in common (Jaccard): "Oat Barista Milk (1L)" vs "Oat milk, 1L" = 0.75."""
    wa, wb = _words(a), _words(b)
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
        for c in price_check(db, invoice).comparisons:
            print(f"  {c['item']}: £{c['unit_price']:.2f} vs £{c['other_price']:.2f} at {c['counterparty']} "
                  f"({c['invoice_number']}): {c['change_pct']:+.1f}%")


if __name__ == "__main__":
    main()
