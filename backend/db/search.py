"""Invoice search on MongoDB Atlas (MVP2): keyword, meaning, and both at once.

Atlas features used (each doing the job it's best at):
- Atlas Search ("invoices_text"): typo-tolerant keyword search; "diary" still finds Eastside Dairy, "EDC-7781" finds that invoice.
  The same index gives highlighting (which words matched), facets ($searchMeta: invoices per supplier), autocomplete
  ("eas" → Eastside Dairy Co) and the duplicate-invoice check on intake ("EDC 7781" vs "EDC-7781").
- More Atlas Search indexes: "products_text" (typo-tolerant product names for price what-ifs), "agent_log_text" (search
  what the agent did), "board_memos_text" and "events_text" (search past board memos and nearby events), and
  "news_text" (local news ranked by how soon and how close with `near` on dates + a geo point, duplicates across
  sources found with `moreLikeThis`, facets by kind) + "news_semantic" (Automated Embedding with Voyage: news searched
  by meaning, "catering partnership" finds "craft baristas for film shoots"; hybrid with keywords via $rankFusion;
  "more like this one" by vector similarity).
- Automated Embedding + Vector Search ("invoices_semantic"): Atlas embeds `search_text` itself with a Voyage AI model on
  every insert/update (no embedding code here) so we can search by meaning: "milk supplier" finds Meadowfield Dairy.
- Hybrid Search ($rankFusion): one search box that merges both rankings.

Without Atlas Search (tests on mongomock, an index missing or still building) every function falls back to a plain
word match and says so (`mode = "basic"`, logged in agent_log at most once an hour), so the dashboard and WhatsApp never
break and the failure is never silent.

Usage (from the repo root):
    python -m backend.db.search --setup              # backfill search_text + create/update the Atlas indexes
    python -m backend.db.search "milk we owe"        # hybrid search
    python -m backend.db.search --similar MF-2087    # similar invoices + price check
    python -m backend.db.search --check              # live self-test against Atlas (run before the demo)
    python -m backend.db.search --activity "Hackney"  # search the agent's own record
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

PRODUCTS_INDEX, ACTIVITY_INDEX, MEMOS_INDEX, EVENTS_INDEX = "products_text", "agent_log_text", "board_memos_text", "events_text"
NEWS_INDEX, NEWS_SEMANTIC = "news_text", "news_semantic"
AUTOCOMPLETE = {"type": "autocomplete", "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15, "foldDiacritics": True}

# name → (collection, kind, definition). Each is created on its own, in this order: a cluster that allows fewer search
# indexes (Atlas free tier: 3) keeps the most useful ones, and every search falls back to a plain match without its index.
INDEXES = {
    TEXT_INDEX: (schema.INVOICES, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "invoice_number": {"type": "string"},
        "counterparty": [{"type": "string", "analyzer": "lucene.english"}, AUTOCOMPLETE, {"type": "token"}],
        "status": {"type": "token"},
        "direction": {"type": "token"},
        "line_items": {"type": "document", "fields": {"description": {"type": "string", "analyzer": "lucene.english"}}},
    }}}),
    SEMANTIC_INDEX: (schema.INVOICES, "vectorSearch", {"fields": [
        {"type": "autoEmbed", "modality": "text", "path": "search_text", "model": EMBEDDING_MODEL},
        {"type": "filter", "path": "business_id"},
        {"type": "filter", "path": "direction"},
    ]}),
    PRODUCTS_INDEX: (schema.PRODUCTS, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "name": [{"type": "string"}, AUTOCOMPLETE],
    }}}),
    ACTIVITY_INDEX: (schema.AGENT_LOG, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "summary": {"type": "string", "analyzer": "lucene.english"},
    }}}),
    MEMOS_INDEX: (schema.BOARD_MEMOS, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "headline": {"type": "string", "analyzer": "lucene.english"},
        "insights": {"type": "document", "fields": {k: {"type": "string", "analyzer": "lucene.english"}
                                                    for k in ("title", "finding", "action")}},
    }}}),
    NEWS_INDEX: (schema.NEWS, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "kind": {"type": "token"},
        "source": {"type": "token"},
        "duplicate_of": {"type": "token"},
        "headline": {"type": "string", "analyzer": "lucene.english"},
        "summary": {"type": "string", "analyzer": "lucene.english"},
        "published_at": {"type": "date"},
        "starts_at": {"type": "date"},
        "expires_at": {"type": "date"},
        "location": {"type": "geo"},
    }}}),
    NEWS_SEMANTIC: (schema.NEWS, "vectorSearch", {"fields": [
        {"type": "autoEmbed", "modality": "text", "path": "search_text", "model": EMBEDDING_MODEL},
        {"type": "filter", "path": "business_id"},
        {"type": "filter", "path": "kind"},
        {"type": "filter", "path": "expires_at"},
    ]}),
    EVENTS_INDEX: (schema.EVENTS, "search", {"mappings": {"dynamic": False, "fields": {
        "business_id": {"type": "token"},
        "status": {"type": "token"},
        **{k: {"type": "string", "analyzer": "lucene.english"} for k in ("name", "venue", "category", "genre")},
    }}}),
}

FIELDS = {"invoice_number": 1, "counterparty": 1, "direction": 1, "amount": 1, "currency": 1, "issue_date": 1,
          "due_date": 1, "status": 1, "needs_review": 1, "line_items": 1, "search_text": 1, "counterparty_key": 1,
          "created_at": 1, "paid_at": 1}


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
    counterparty: str = ""
    supplier: dict = field(default_factory=dict)  # supplier_history(): the relationship behind the numbers
    preferred: bool = False  # the owner is loyal to this supplier: no "cheaper elsewhere" suggestions

    @property
    def rises(self) -> List[dict]:
        return [c for c in self.comparisons if c["kind"] == "same_supplier" and c["change_pct"] >= RISE_PCT]

    @property
    def cheaper_elsewhere(self) -> List[dict]:
        if self.preferred:
            return []
        return [c for c in self.comparisons if c["kind"] == "other_supplier" and c["change_pct"] >= RISE_PCT]

    def lines(self) -> List[str]:
        """Owner-facing sentences. Loyalty first: a cheaper price elsewhere is a reason to ask for a match, never
        advice to switch (the relationship, reliability and quality aren't in the numbers)."""
        out = [f"{self.counterparty} put {c['item']} up {c['change_pct']:.1f}% (£{c['other_price']:.2f} → "
               f"£{c['unit_price']:.2f} since {c['invoice_number']})." for c in self.rises]
        for c in self.cheaper_elsewhere:
            # Name both items when they differ ("Oat Barista Milk" isn't plain "Oat milk"): the owner judges quality
            mine = "here" if c["exact"] else f"for your {c['item']} (similar, not identical)"
            out.append(f"{c['counterparty']} charged £{c['other_price']:.2f} for {c['compared_to']} vs £{c['unit_price']:.2f} "
                       f"{mine}: worth asking {self.counterparty} to match{_relationship(self.supplier)}.")
        return out


def ensure_search_indexes(db: Database) -> Dict[str, str]:
    """Backfill `search_text` and create (or update) the Atlas Search indexes, each on its own. Safe to re-run.
    Returns {index name: status, or "refused: why"}; {} when the server has no Atlas Search (e.g. mongomock)."""
    invoices = db[schema.INVOICES]
    for doc in invoices.find({"search_text": {"$exists": False}}):
        invoices.update_one({"_id": doc["_id"]}, {"$set": {"search_text": schema.invoice_search_text(doc)}})
    try:
        invoices.list_search_indexes().try_next()
    except (OperationFailure, NotImplementedError, AttributeError, TypeError):  # TypeError: mongomock has no such method
        return {}
    out = {}
    for name, (coll, kind, definition) in INDEXES.items():
        try:
            if coll not in db.list_collection_names():  # Atlas only indexes a collection that exists
                db.create_collection(coll)
            existing = {i["name"]: i for i in db[coll].list_search_indexes()}
            if name not in existing:
                db[coll].create_search_index(SearchIndexModel(definition, name=name, type=kind))
            elif existing[name].get("latestDefinition") != definition:
                db[coll].update_search_index(name, definition)
            out[name] = next((i.get("status", "?") for i in db[coll].list_search_indexes() if i["name"] == name), "?")
        except OperationFailure as e:  # e.g. the cluster's search index limit: keep going, the search falls back
            out[name] = f"refused: {str(e)[:160]}"
    return out


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
        basic = _basic_search(db, query, business_id, limit)
        highlight_invoices(db, query, basic, business_id)
        return _fallback(db, basic, str(e)[:200])
    hits = _relevant(docs)[:limit]
    if not hits:  # Atlas answers a missing or still-building index with no results, not an error: double-check
        basic = _basic_search(db, query, business_id, limit)
        if basic:
            return _fallback(db, basic, "Atlas Search returned nothing but a plain match did (index missing or building?)")
    highlight_invoices(db, query, hits, business_id)
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
    check = PriceCheck(invoice.get("invoice_number", ""), counterparty=invoice.get("counterparty") or "This supplier")
    if invoice.get("needs_review"):
        check.skipped = "the invoice still needs the owner's check, so its prices aren't trusted yet"
        return check
    similar = similar_invoices(db, invoice, limit=8)
    check.mode = similar.mode
    key = invoice.get("counterparty_key") or schema.counterparty_key(invoice.get("counterparty"))
    # The supplier's own earlier bills matter most, so fetch them directly rather than hoping they rank in the top 8
    own = list(db[schema.INVOICES].find({"business_id": invoice.get("business_id", BUSINESS_ID), "counterparty_key": key,
                                         "direction": invoice.get("direction"), "_id": {"$ne": invoice.get("_id")}}, FIELDS))
    check.supplier = supplier_history(own, invoice)
    business = db[schema.BUSINESSES].find_one({"_id": invoice.get("business_id", BUSINESS_ID)}) or {}
    check.preferred = key in (business.get("preferred_suppliers") or [])
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
            match = _best_match(item, pool)
            if match:
                other, li = match
                check.comparisons.append({
                    "exact": _overlap(item["description"], li["description"]) == 1.0,
                    "kind": kind, "item": item["description"], "unit_price": item["unit_price"], "qty": item.get("qty") or 0,
                    "compared_to": li["description"], "other_price": li["unit_price"],
                    "counterparty": other.get("counterparty"), "invoice_number": other.get("invoice_number"),
                    "change_pct": round((item["unit_price"] - li["unit_price"]) / li["unit_price"] * 100, 1),
                })
    return check


def supplier_history(bills: List[dict], invoice: dict) -> dict:
    """The relationship with this supplier from their earlier bills: how long, how many, how much, paid on time."""
    earlier = [b for b in bills if _when(b) < _when(invoice)]
    paid = [b for b in earlier if b.get("status") == "paid" and b.get("paid_at") and b.get("due_date")]
    on_time = [b for b in paid if _aware(b["paid_at"]) <= _aware(b["due_date"])]
    now = datetime.now(timezone.utc)
    late = [b for b in earlier if b.get("status") != "paid" and b.get("due_date") and _aware(b["due_date"]) < now]
    return {"bills": len(earlier), "since": min((_when(b) for b in earlier), default=None),
            "spent": round(sum(b.get("amount") or 0 for b in earlier), 2), "paid": len(paid), "paid_on_time": len(on_time),
            "overdue": len(late)}


def set_preferred(db: Database, counterparty: str, preferred: bool = True, business_id: str = BUSINESS_ID) -> List[str]:
    """Mark (or unmark) a supplier the owner is loyal to. Returns the new list of preferred supplier keys."""
    op = "$addToSet" if preferred else "$pull"
    db[schema.BUSINESSES].update_one({"_id": business_id}, {op: {"preferred_suppliers": schema.counterparty_key(counterparty)}})
    return (db[schema.BUSINESSES].find_one({"_id": business_id}) or {}).get("preferred_suppliers", [])


# ---------------------------------------------------------------- more Atlas Search: highlights, facets,
# autocomplete, duplicates, products, the agent's record, board memos and events

MAX_SUGGEST = 6
PRODUCT_LEAD = 1.5  # the top product must score this many times the next to be picked without asking
ACTIVITY_LIMIT = 10


def _clauses(query: str, fields: List[str]) -> List[dict]:
    """Exact words (boosted) + typo tolerance for words of 5+ letters, over any fields."""
    clauses = [{"text": {"query": query, "path": fields, "score": {"boost": {"value": 2}}}}]
    long_words = [w for w in re.findall(r"[A-Za-z]+", query) if len(w) >= FUZZY_MIN_LEN]
    if long_words:
        clauses.append({"text": {"query": " ".join(long_words), "path": fields, "fuzzy": {"maxEdits": 1}}})
    return clauses


def _snippets(highlights: Optional[List[dict]], limit: int = 2) -> List[List[dict]]:
    """Atlas `searchHighlights` → [[{"value": "Oat ", "hit": False}, {"value": "milk", "hit": True}, …], …], best first.
    Plain values: the template escapes them and wraps hits in <mark> (never raw HTML from an email)."""
    best = sorted(highlights or [], key=lambda h: -(h.get("score") or 0))[:limit]
    return [[{"value": t.get("value", ""), "hit": t.get("type") == "hit"} for t in h.get("texts") or []] for h in best]


def _basic_snippets(texts: List[str], query: str, limit: int = 2) -> List[List[dict]]:
    """The same shape as _snippets, by plain word match (no Atlas)."""
    words = _words(query)
    out = []
    for text in texts:
        parts = re.split(r"([A-Za-z0-9]+)", text or "")
        if any(p.lower() in words for p in parts):
            out.append([{"value": p, "hit": p.lower() in words} for p in parts if p])
        if len(out) == limit:
            break
    return out


def highlight_invoices(db: Database, query: str, hits: List[dict], business_id: str = BUSINESS_ID) -> None:
    """Add `highlights` (which words matched, in the supplier name or a line item) to keyword hits, in place.
    Cosmetic: a failure leaves the hits without highlights."""
    keyword = [h for h in hits if "keyword" in (h.get("matched_by") or [])]
    if not keyword:
        return
    try:
        found = {d.get("_id"): d.get("highlights") for d in db[schema.INVOICES].aggregate([
            {"$search": {"index": TEXT_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                "should": _keyword_clauses(query), "minimumShouldMatch": 1},
                "highlight": {"path": ["counterparty", "line_items.description"]}}},
            {"$match": {"_id": {"$in": [h.get("_id") for h in keyword]}}},
            {"$project": {"highlights": {"$meta": "searchHighlights"}}}])}
        for h in keyword:
            h["highlights"] = _snippets(found.get(h.get("_id")))
    except (OperationFailure, NotImplementedError, TypeError, AttributeError):  # cosmetic: never fail a search over it
        for h in keyword:
            h["highlights"] = _basic_snippets([h.get("counterparty") or ""] +
                                              [li.get("description") or "" for li in h.get("line_items") or []], query)


def invoice_facets(db: Database, business_id: str = BUSINESS_ID, buckets: int = 6) -> dict:
    """Counts across all invoices from one $searchMeta call: by status, by direction, and the busiest counterparties.
    {"total": 16, "status": [("paid", 8), …], "direction": […], "counterparty": [("Eastside Dairy Co", 5), …], "mode"}"""
    names = {"status": "status", "direction": "direction", "counterparty": "counterparty"}
    try:
        meta = next(db[schema.INVOICES].aggregate([{"$searchMeta": {"index": TEXT_INDEX, "facet": {
            "operator": {"equals": {"path": "business_id", "value": business_id}},
            "facets": {k: {"type": "string", "path": path, "numBuckets": buckets} for k, path in names.items()}}}}]), None)
        if not meta:
            raise OperationFailure("no $searchMeta result")
        out = {k: [(b["_id"], b["count"]) for b in meta["facet"][k]["buckets"]] for k in names}
        return {"total": meta["count"]["lowerBound"], **out, "mode": "atlas"}
    except (OperationFailure, NotImplementedError, KeyError, TypeError):
        from collections import Counter
        docs = list(db[schema.INVOICES].find({"business_id": business_id}, {k: 1 for k in names}))
        out = {k: Counter(d.get(k) for d in docs if d.get(k)).most_common(buckets) for k in names}
        return {"total": len(docs), **out, "mode": "basic"}


def suggest(db: Database, prefix: str, business_id: str = BUSINESS_ID, limit: int = MAX_SUGGEST) -> SearchResult:
    """Autocomplete for the search box: supplier/customer names starting with what's typed ("eas" → Eastside Dairy Co),
    then product names. Names only, never amounts."""
    prefix = re.sub(r"\s+", " ", (prefix or "")).strip()[:40]
    if len(prefix) < 2 or not re.search(r"\w", prefix):
        return SearchResult([], "atlas")
    fuzzy = {"fuzzy": {"maxEdits": 1, "prefixLength": 2}} if len(prefix) >= 4 else {}
    try:
        names = []
        for coll, index, path in ((schema.INVOICES, TEXT_INDEX, "counterparty"), (schema.PRODUCTS, PRODUCTS_INDEX, "name")):
            for d in db[coll].aggregate([
                    {"$search": {"index": index, "compound": {
                        "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                        "must": [{"autocomplete": {"query": prefix, "path": path, **fuzzy}}]}}},
                    {"$limit": limit * 3}, {"$project": {path: 1}}]):
                names.append((d.get(path), "product" if coll == schema.PRODUCTS else "counterparty"))
        return SearchResult(_unique_names(names, limit), "atlas")
    except (OperationFailure, NotImplementedError) as e:
        low = prefix.lower()
        names = [(n, "counterparty") for n in db[schema.INVOICES].distinct("counterparty", {"business_id": business_id})] + \
                [(n, "product") for n in db[schema.PRODUCTS].distinct("name", {"business_id": business_id})]
        starts = [x for x in names if x[0] and (x[0].lower().startswith(low) or any(w.startswith(low) for w in x[0].lower().split()))]
        return _fallback(db, _unique_names(sorted(starts, key=lambda x: x[0]), limit), str(e)[:200])


def _unique_names(names: List[tuple], limit: int) -> List[dict]:
    seen, out = set(), []
    for name, kind in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "kind": kind})
    return out[:limit]


def number_key(number: Optional[str]) -> str:
    """"EDC 7781", "edc-7781", "EDC/7781" → "edc7781": one invoice number however it's written."""
    return re.sub(r"[^a-z0-9]", "", (number or "").lower())


def _same_supplier(a: dict, b: dict) -> bool:
    """"Eastside Dairy" and "Eastside Dairy Co" are one supplier; "Eastside Dairy" and "Westside Dairy" are not."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return False
    return ka == kb or ka.startswith(kb) or kb.startswith(ka) or \
        _overlap(a.get("counterparty"), b.get("counterparty")) >= 0.6


def is_duplicate(doc: dict, other: dict) -> bool:
    """Same invoice written differently: the same number (ignoring spaces/dashes/case), the same amount, the same
    direction and the same supplier or a close spelling of it. A monthly bill with the same amount but a new number is
    NOT a duplicate. An exact match (same supplier key + number) is left to the unique index."""
    if number_key(doc.get("invoice_number")) != number_key(other.get("invoice_number")) \
            or number_key(doc.get("invoice_number")).startswith("unknown") or not number_key(doc.get("invoice_number")):
        return False
    if doc.get("direction") != other.get("direction") or (doc.get("currency") or "GBP") != (other.get("currency") or "GBP"):
        return False
    if not (_positive(doc.get("amount")) and _positive(other.get("amount"))) or abs(doc["amount"] - other["amount"]) > 0.005:
        return False
    exact = _key(doc) == _key(other) and doc.get("invoice_number") == other.get("invoice_number")
    return not exact and _same_supplier(doc, other)


def possible_duplicate(db: Database, doc: dict) -> Optional[dict]:
    """An invoice already stored that is probably this one written differently (see is_duplicate), or None.
    Atlas Search finds the candidates by invoice number ("EDC 7781" matches "EDC-7781"); code makes the call."""
    business_id = doc.get("business_id", BUSINESS_ID)
    number = (doc.get("invoice_number") or "").strip()
    if not number_key(number) or number_key(number).startswith("unknown"):
        return None
    spaced = " ".join(re.findall(r"[A-Za-z]+|\d+", number))  # "EDC7781" → "EDC 7781": the tokens Atlas indexed
    try:
        candidates = list(db[schema.INVOICES].aggregate([
            {"$search": {"index": TEXT_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}},
                           {"equals": {"path": "direction", "value": doc.get("direction") or ""}}],
                "should": [{"text": {"query": q, "path": "invoice_number"}} for q in {number, spaced}],
                "minimumShouldMatch": 1}}},
            {"$limit": 20}, {"$project": FIELDS}]))
        # Atlas returns nothing (not an error) while an index is missing or building: double-check by hand then
        if not candidates:
            raise OperationFailure("no candidates from Atlas Search")
    except (OperationFailure, NotImplementedError):
        key = number_key(number)
        candidates = [d for d in db[schema.INVOICES].find({"business_id": business_id, "direction": doc.get("direction")}, FIELDS)
                      if number_key(d.get("invoice_number")) == key]
    return next((c for c in candidates if c.get("_id") != doc.get("_id") and is_duplicate(doc, c)), None)


def find_products(db: Database, query: str, business_id: str = BUSINESS_ID, limit: int = 5) -> SearchResult:
    """Menu items by name, typo-tolerant: "flat whte" → Flat White, "capuccino" → Cappuccino, "flat wh" (as typed)."""
    query = (query or "").strip()[:60]
    if not re.search(r"\w", query):
        return SearchResult([], "atlas")
    words = re.findall(r"[A-Za-z]+", query)
    should = [{"text": {"query": query, "path": "name", "score": {"boost": {"value": 3}}}},
              {"autocomplete": {"query": query, "path": "name"}}]
    if any(len(w) >= 4 for w in words):
        should.append({"text": {"query": " ".join(w for w in words if len(w) >= 4), "path": "name", "fuzzy": {"maxEdits": 2 if any(len(w) >= 7 for w in words) else 1}}})
    try:
        docs = list(db[schema.PRODUCTS].aggregate([
            {"$search": {"index": PRODUCTS_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}], "should": should, "minimumShouldMatch": 1}}},
            {"$limit": limit}, {"$project": {"name": 1, "price": 1, "score": {"$meta": "searchScore"}}}]))
    except (OperationFailure, NotImplementedError) as e:
        return _fallback(db, _basic_products(db, query, business_id, limit), str(e)[:200])
    if not docs:  # a missing or building index answers with nothing: only a failure if a plain match does find it
        basic = _basic_products(db, query, business_id, limit)
        if basic:
            return _fallback(db, basic, "Atlas Search returned no products but a plain match did (index missing or building?)")
    return SearchResult(docs, "atlas")


def _basic_products(db: Database, query: str, business_id: str, limit: int) -> List[dict]:
    wanted = query.lower()
    return [{**p, "score": 1.0} for p in db[schema.PRODUCTS].find({"business_id": business_id}, {"name": 1, "price": 1})
            if wanted in (p.get("name") or "").lower() or (p.get("name") or "").lower() in wanted][:limit]


def pick_product(query: str, result: SearchResult) -> tuple:
    """(the product, None) when the match is clear, else (None, [names to ask about]). An exact name always wins;
    otherwise the top hit must clearly lead ("matcha" → Hot or Iced? ask)."""
    hits = result.hits
    exact = [h for h in hits if (h.get("name") or "").lower() == (query or "").strip().lower()]
    if exact:
        return exact[0], None
    if len(hits) == 1:
        return hits[0], None
    if len(hits) >= 2 and result.mode == "atlas" and hits[0]["score"] >= PRODUCT_LEAD * hits[1]["score"]:
        return hits[0], None
    return None, [h["name"] for h in hits]


def search_activity(db: Database, query: str, business_id: str = BUSINESS_ID, limit: int = ACTIVITY_LIMIT) -> SearchResult:
    """The agent's own record, searched: "what did you do about Hackney Hub?" → the reminders drafted, the decisions.
    Hits: {"summary", "kind", "at", "actor", "score", "highlights"}, best match first."""
    query = (query or "").strip()[:MAX_QUERY_LEN]
    if not re.search(r"\w", query):
        return SearchResult([], "atlas")
    try:
        docs = list(db[schema.AGENT_LOG].aggregate([
            {"$search": {"index": ACTIVITY_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                "should": _clauses(query, ["summary"]), "minimumShouldMatch": 1},
                "highlight": {"path": "summary"}}},
            {"$limit": limit},
            {"$project": {"summary": 1, "kind": 1, "at": 1, "actor": 1, "score": {"$meta": "searchScore"},
                          "highlights": {"$meta": "searchHighlights"}}}]))
        for d in docs:
            d["highlights"] = _snippets(d.get("highlights"), limit=1)
        if not docs and _basic_activity(db, query, business_id, 1):
            raise OperationFailure("Atlas Search returned nothing but a plain match did (index missing or building?)")
        return SearchResult(docs, "atlas")
    except (OperationFailure, NotImplementedError) as e:
        return _fallback(db, _basic_activity(db, query, business_id, limit), str(e)[:200])


def _basic_activity(db: Database, query: str, business_id: str, limit: int) -> List[dict]:
    words = _words(query)
    hits = []
    for e in db[schema.AGENT_LOG].find({"business_id": business_id}, {"summary": 1, "kind": 1, "at": 1, "actor": 1}).sort("at", -1).limit(500):
        score = len(words & _words(e.get("summary")))
        if score:
            hits.append({**e, "score": float(score), "highlights": _basic_snippets([e.get("summary") or ""], query, 1)})
    return sorted(hits, key=lambda d: -d["score"])[:limit]


def search_notes(db: Database, query: str, business_id: str = BUSINESS_ID, limit: int = 5) -> dict:
    """Past board memo insights and nearby events matching the query:
    {"insights": [{"date", "title", "finding", "action", "status"}], "events": [{"name", "venue", "date", …}], "mode"}."""
    query = (query or "").strip()[:MAX_QUERY_LEN]
    if not re.search(r"\w", query):
        return {"insights": [], "events": [], "mode": "atlas"}
    words, mode = _words(query), "atlas"
    try:
        memos = list(db[schema.BOARD_MEMOS].aggregate([
            {"$search": {"index": MEMOS_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                "should": _clauses(query, ["headline", "insights.title", "insights.finding", "insights.action"]),
                "minimumShouldMatch": 1}}},
            {"$limit": limit}, {"$project": {"date": 1, "insights": 1}}]))
        events = list(db[schema.EVENTS].aggregate([
            {"$search": {"index": EVENTS_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                "mustNot": [{"equals": {"path": "status", "value": "removed"}}],
                "should": _clauses(query, ["name", "venue", "category", "genre"]), "minimumShouldMatch": 1}}},
            {"$limit": limit}, {"$project": {"name": 1, "venue": 1, "date": 1, "category": 1, "distance_km": 1}}]))
    except (OperationFailure, NotImplementedError) as e:
        mode = "basic"
        _fallback(db, [], str(e)[:200])
        memos = [m for m in db[schema.BOARD_MEMOS].find({"business_id": business_id}).sort("date", -1).limit(30)
                 if words & _words(" ".join([m.get("headline") or ""] + [" ".join(str(i.get(k) or "") for k in ("title", "finding", "action"))
                                                                         for i in m.get("insights") or []]))][:limit]
        events = [e for e in db[schema.EVENTS].find({"business_id": business_id, "status": {"$ne": "removed"}})
                  if words & _words(" ".join(str(e.get(k) or "") for k in ("name", "venue", "category", "genre")))][:limit]
    insights = []
    for m in memos:  # the memo matched; show the insights in it that mention the query (all of them if none does)
        found = [i for i in m.get("insights") or []
                 if words & _words(" ".join(str(i.get(k) or "") for k in ("title", "finding", "action")))] or m.get("insights") or []
        insights += [{"date": m.get("date"), **{k: i.get(k) for k in ("title", "finding", "action", "status")}} for i in found]
    return {"insights": insights[:limit], "events": [{k: e.get(k) for k in ("name", "venue", "date", "category", "distance_km")}
                                                     for e in events], "mode": mode}



# ---------------------------------------------------------------- local news: ranked by how soon and how close

SOON_PIVOT_MS = 3 * 24 * 3600 * 1000  # an event 3 days away scores half as much as one today
FRESH_PIVOT_MS = 7 * 24 * 3600 * 1000  # a story a week old scores half as much as today's
NEAR_PIVOT_M = 1000  # a place 1 km from the pitch scores half as much as one at the pitch
NEWS_FIELDS = {"kind": 1, "headline": 1, "summary": 1, "url": 1, "source": 1, "published_at": 1, "starts_at": 1,
               "distance_km": 1, "expires_at": 1, "search_text": 1}


def _news_filter(business_id: str, now: datetime, kind: Optional[str]) -> List[dict]:
    out = [{"equals": {"path": "business_id", "value": business_id}},
           {"range": {"path": "expires_at", "gte": now}}]
    if kind:
        out.append({"equals": {"path": "kind", "value": kind}})
    return out


def news_feed(db: Database, query: str = "", kind: Optional[str] = None, now: Optional[datetime] = None,
              business_id: str = BUSINESS_ID, limit: int = 8) -> SearchResult:
    """The owner's local news, best first: events coming up soon, fresh stories and places near the pitch rank highest
    (Atlas Search `near` on starts_at, published_at and the geo location). With a query: typo-tolerant text search,
    matched words highlighted. Expired items and duplicates of another source's story are left out."""
    from backend.inputs.pitches import PITCHES

    now = now or datetime.now(timezone.utc)
    query = (query or "").strip()[:MAX_QUERY_LEN]
    pitch = PITCHES[0]
    should = [{"near": {"path": "starts_at", "origin": now, "pivot": SOON_PIVOT_MS, "score": {"boost": {"value": 2}}}},
              {"near": {"path": "published_at", "origin": now, "pivot": FRESH_PIVOT_MS}},
              {"near": {"path": "location", "origin": {"type": "Point", "coordinates": [pitch["lon"], pitch["lat"]]},
                        "pivot": NEAR_PIVOT_M}}]
    compound = {"filter": _news_filter(business_id, now, kind), "mustNot": [{"exists": {"path": "duplicate_of"}}],
                "should": should}
    if query:
        compound["must"] = [{"compound": {"should": _clauses(query, ["headline", "summary"]), "minimumShouldMatch": 1}}]
    stage = {"index": NEWS_INDEX, "compound": compound}
    if query:
        stage["highlight"] = {"path": ["headline", "summary"]}
    try:
        docs = list(db[schema.NEWS].aggregate([
            {"$search": stage}, {"$limit": limit},
            {"$project": {**NEWS_FIELDS, "score": {"$meta": "searchScore"},
                          **({"highlights": {"$meta": "searchHighlights"}} if query else {})}}]))
        for d in docs:
            d["highlights"] = _snippets(d.get("highlights"), limit=1) if query else []
        if not docs:  # a missing or building index answers with nothing: only a failure if a plain match finds some
            basic = _basic_news(db, query, kind, now, business_id, limit)
            if basic:
                return _fallback(db, basic, "Atlas Search returned no news but a plain match did (index missing or building?)")
        return SearchResult(docs, "atlas")
    except (OperationFailure, NotImplementedError) as e:
        return _fallback(db, _basic_news(db, query, kind, now, business_id, limit), str(e)[:200])


def news_search(db: Database, query: str, kind: Optional[str] = None, now: Optional[datetime] = None,
                business_id: str = BUSINESS_ID, limit: int = 8) -> SearchResult:
    """Local news by words AND meaning (Hybrid Search: Atlas Search + Vector Search with Automated Embedding, merged with
    $rankFusion). "catering partnership" also finds "Film and TV shoots hire craft baristas". Meaning-only hits must be
    close enough (as for invoices); expired items and duplicates are left out. Falls back to the keyword feed."""
    now = now or datetime.now(timezone.utc)
    query = (query or "").strip()[:MAX_QUERY_LEN]
    if not re.search(r"\w", query):
        return news_feed(db, "", kind, now, business_id, limit)
    vector_filter = {"business_id": business_id, "expires_at": {"$gte": now}, **({"kind": kind} if kind else {})}
    pipelines = {
        "keyword": [{"$search": {"index": NEWS_INDEX, "compound": {
            "filter": _news_filter(business_id, now, kind), "mustNot": [{"exists": {"path": "duplicate_of"}}],
            "should": _clauses(query, ["headline", "summary"]), "minimumShouldMatch": 1}}}, {"$limit": limit * 2}],
        "meaning": [{"$vectorSearch": {"index": NEWS_SEMANTIC, "path": "search_text", "query": {"text": query},
                                       "filter": vector_filter, "numCandidates": 100, "limit": limit * 2}}],
    }
    try:
        docs = list(db[schema.NEWS].aggregate([
            {"$rankFusion": {"input": {"pipelines": pipelines}, "scoreDetails": True}},
            {"$project": {**NEWS_FIELDS, "duplicate_of": 1, "score": {"$meta": "score"}, "details": {"$meta": "scoreDetails"}}}]))
    except (OperationFailure, NotImplementedError):
        return news_feed(db, query, kind, now, business_id, limit)  # keyword search (and its own fallback)
    hits = [d for d in _relevant(docs) if not d.get("duplicate_of")][:limit]
    if not hits:
        return news_feed(db, query, kind, now, business_id, limit)
    _highlight_news(db, query, hits, business_id, now)
    return SearchResult(hits, "hybrid")


def _highlight_news(db: Database, query: str, hits: List[dict], business_id: str, now: datetime) -> None:
    keyword = [h for h in hits if "keyword" in (h.get("matched_by") or [])]
    for h in hits:
        h["highlights"] = []
    if not keyword:
        return
    try:
        found = {d.get("_id"): d.get("highlights") for d in db[schema.NEWS].aggregate([
            {"$search": {"index": NEWS_INDEX, "compound": {"filter": _news_filter(business_id, now, None),
                                                           "should": _clauses(query, ["headline", "summary"]), "minimumShouldMatch": 1},
                         "highlight": {"path": ["headline", "summary"]}}},
            {"$match": {"_id": {"$in": [h.get("_id") for h in keyword]}}},
            {"$project": {"highlights": {"$meta": "searchHighlights"}}}])}
        for h in keyword:
            h["highlights"] = _snippets(found.get(h.get("_id")), limit=1)
    except (OperationFailure, NotImplementedError, TypeError, AttributeError):
        for h in keyword:
            h["highlights"] = _basic_snippets([h.get("headline") or "", h.get("summary") or ""], query, 1)


def similar_news(db: Database, news_id: str, now: Optional[datetime] = None, business_id: str = BUSINESS_ID,
                 limit: int = 5) -> SearchResult:
    """"More like this one": live news closest in meaning to one item (Vector Search on its Voyage embedding).
    Without vector search: the items sharing the most words."""
    now = now or datetime.now(timezone.utc)
    me = db[schema.NEWS].find_one({"_id": news_id, "business_id": business_id})
    if not me:
        return SearchResult([], "atlas")
    try:
        docs = list(db[schema.NEWS].aggregate([
            {"$vectorSearch": {"index": NEWS_SEMANTIC, "path": "search_text", "query": {"text": me.get("search_text") or me["headline"]},
                               "filter": {"business_id": business_id, "expires_at": {"$gte": now}}, "numCandidates": 100, "limit": limit + 6}},
            {"$project": {**NEWS_FIELDS, "duplicate_of": 1, "score": {"$meta": "vectorSearchScore"}}}]))
        hits = [d for d in docs if d.get("_id") != news_id and not d.get("duplicate_of")][:limit]
        if not hits and db[schema.NEWS].count_documents({"business_id": business_id, "_id": {"$ne": news_id}}, limit=1):
            raise OperationFailure("Vector Search returned nothing (index missing or building?)")
        return SearchResult(hits, "atlas")
    except (OperationFailure, NotImplementedError) as e:
        mine = _words(me.get("search_text") or me.get("headline"))
        others = []
        for d in db[schema.NEWS].find({"business_id": business_id, "_id": {"$ne": news_id}, "duplicate_of": {"$exists": False}}, NEWS_FIELDS):
            if d.get("expires_at") and _aware(d["expires_at"]) >= now:
                score = _overlap(" ".join(mine), d.get("search_text") or f"{d.get('headline', '')} {d.get('summary', '')}")
                if score:
                    others.append({**d, "score": round(score, 3)})
        return _fallback(db, sorted(others, key=lambda d: -d["score"])[:limit], str(e)[:200])


def _basic_news(db: Database, query: str, kind: Optional[str], now: datetime, business_id: str, limit: int) -> List[dict]:
    """The same ranking by hand: soon + fresh + near, the way Atlas `near` scores them (pivot / (pivot + distance))."""
    words = _words(query)
    out = []
    for d in db[schema.NEWS].find({"business_id": business_id, "duplicate_of": {"$exists": False},
                                   **({"kind": kind} if kind else {})}, NEWS_FIELDS):
        if not d.get("expires_at") or _aware(d["expires_at"]) < now:
            continue
        text = f"{d.get('headline', '')} {d.get('summary', '')}"
        if words and not words & _words(text):
            continue
        score = 0.0
        if d.get("starts_at"):
            score += 2 * SOON_PIVOT_MS / (SOON_PIVOT_MS + abs((_aware(d["starts_at"]) - now).total_seconds() * 1000))
        if d.get("published_at"):
            score += FRESH_PIVOT_MS / (FRESH_PIVOT_MS + abs((_aware(d["published_at"]) - now).total_seconds() * 1000))
        if d.get("distance_km") is not None:
            score += NEAR_PIVOT_M / (NEAR_PIVOT_M + d["distance_km"] * 1000)
        out.append({**d, "score": round(score, 4),
                    "highlights": _basic_snippets([d.get("headline") or "", d.get("summary") or ""], query, 1) if words else []})
    return sorted(out, key=lambda d: -d["score"])[:limit]


def news_facets(db: Database, now: Optional[datetime] = None, business_id: str = BUSINESS_ID) -> dict:
    """{"kind": [("event", 3), …], "total": n, "mode"}: one $searchMeta over the live (unexpired, not duplicate) news."""
    now = now or datetime.now(timezone.utc)
    try:
        meta = next(db[schema.NEWS].aggregate([{"$searchMeta": {"index": NEWS_INDEX, "facet": {
            "operator": {"compound": {"filter": _news_filter(business_id, now, None),
                                      "mustNot": [{"exists": {"path": "duplicate_of"}}]}},
            "facets": {"kind": {"type": "string", "path": "kind"}}}}}]), None)
        if not meta:
            raise OperationFailure("no $searchMeta result")
        return {"kind": [(b["_id"], b["count"]) for b in meta["facet"]["kind"]["buckets"]],
                "total": meta["count"]["lowerBound"], "mode": "atlas"}
    except (OperationFailure, NotImplementedError, KeyError, TypeError):
        from collections import Counter
        live = [d for d in db[schema.NEWS].find({"business_id": business_id, "duplicate_of": {"$exists": False}}, {"kind": 1, "expires_at": 1})
                if d.get("expires_at") and _aware(d["expires_at"]) >= now]
        return {"kind": Counter(d.get("kind") for d in live).most_common(), "total": len(live), "mode": "basic"}


SAME_STORY = 0.6  # share of meaningful headline words two links must have in common to be one story
NEWS_STOPWORDS = {"at", "in", "on", "to", "by", "with", "from", "is", "are", "as", "its", "new", "this", "that", "after"}


def _headline_overlap(a: Optional[str], b: Optional[str]) -> float:
    """Like _overlap, without the little words: "Coffee carts at weddings" vs "Coffee carts at film shoots" = 0.4."""
    wa = {w for w in _words(a) if not w[0].isdigit() and w not in NEWS_STOPWORDS}
    wb = {w for w in _words(b) if not w[0].isdigit() and w not in NEWS_STOPWORDS}
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def is_same_story(doc: dict, other: dict) -> bool:
    """Two links, one story: most meaningful headline words shared (≥ 0.6) AND the same event day (±1) or, for plain
    news, published within 2 days. "Halloween market at London Fields" (Ticketmaster) = "London Fields Halloween market"
    (paper); "Coffee carts at weddings" ≠ "Coffee carts at film shoots"."""
    if doc.get("_id") == other.get("_id") or _headline_overlap(doc.get("headline"), other.get("headline")) < SAME_STORY:
        return False
    a, b = doc.get("starts_at"), other.get("starts_at")
    if a and b:
        return abs((_aware(a) - _aware(b)).total_seconds()) <= 24 * 3600
    if a or b:
        return False
    pa, pb = doc.get("published_at"), other.get("published_at")
    return bool(pa and pb) and abs((_aware(pa) - _aware(pb)).total_seconds()) <= 2 * 24 * 3600


def same_story(db: Database, doc: dict, business_id: str = BUSINESS_ID) -> Optional[dict]:
    """An already stored item telling the same story (another source), or None. Atlas `moreLikeThis` finds candidates
    that read alike; is_same_story makes the call."""
    fields = {"headline": 1, "starts_at": 1, "published_at": 1, "duplicate_of": 1}
    try:
        candidates = list(db[schema.NEWS].aggregate([
            {"$search": {"index": NEWS_INDEX, "compound": {
                "filter": [{"equals": {"path": "business_id", "value": business_id}}],
                "mustNot": [{"exists": {"path": "duplicate_of"}}],
                "must": [{"moreLikeThis": {"like": {"headline": doc.get("headline") or "", "summary": doc.get("summary") or ""}}}]}}},
            {"$limit": 10}, {"$project": fields}]))
        if not candidates:  # a missing or building index answers with nothing: check by hand rather than miss a twin
            raise OperationFailure("no candidates from Atlas Search")
    except (OperationFailure, NotImplementedError):
        candidates = list(db[schema.NEWS].find({"business_id": business_id}, fields).sort("published_at", -1).limit(300))
    return next((c for c in candidates if not c.get("duplicate_of") and is_same_story(doc, c)), None)


# ---------------------------------------------------------------- helpers

def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _relationship(history: dict) -> str:
    """ " (5 bills since Jul, £740 spent, all paid on time)" or "" when there's no history."""
    if not history.get("bills"):
        return ""
    paid = history["paid"]
    on_time = "all paid on time" if paid and history["paid_on_time"] == paid else \
              f"{history['paid_on_time']} of {paid} paid on time" if paid else "none paid yet"
    late = f", {history['overdue']} overdue now" if history.get("overdue") else ""
    return f" ({history['bills']} bill{'s' if history['bills'] != 1 else ''} since {history['since']:%b}, " \
           f"£{history['spent']:,.0f} spent, {on_time}{late})"


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
    return _aware(doc.get("issue_date") or doc.get("created_at") or datetime.now(timezone.utc))


def _comparable(invoice: dict, other: dict) -> bool:
    """Only earlier, trusted invoices in the same currency are a fair price reference."""
    return (not other.get("needs_review")
            and (other.get("currency") or "GBP") == (invoice.get("currency") or "GBP")
            and _when(other) < _when(invoice))


def _best_match(item: dict, pool: List[dict]):
    """The closest line item with the same pack size: highest word overlap, then the most recent invoice (an old
    price is no fair reference), then the most similar one. None when nothing is close enough."""
    best, best_rank = None, None
    for position, other in enumerate(pool):
        for li in other.get("line_items") or []:
            overlap = _overlap(item.get("description"), li.get("description"))
            if overlap < MIN_ITEM_MATCH or not _positive(li.get("unit_price")) \
                    or _sizes(item.get("description")) != _sizes(li.get("description")):
                continue
            rank = (overlap, _when(other).timestamp(), -position)
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


def live_check(db: Database) -> List[tuple]:
    """Before the demo: are the indexes up and does each search mode really answer? Returns (ok, what) pairs."""
    statuses = {}
    for coll in {c for c, _k, _d in INDEXES.values()}:
        statuses.update({i["name"]: i.get("status") for i in db[coll].list_search_indexes()})
    results = [(statuses.get(n) == "READY", f"index {n}: {statuses.get(n, 'missing')}") for n in INDEXES]
    typo = search_invoices(db, "diary")
    results.append((typo.mode == "hybrid" and any("keyword" in h["matched_by"] and "Dairy" in h["counterparty"] for h in typo.hits),
                    f"keyword with a typo: 'diary' → {[h['counterparty'] for h in typo.hits][:3]} ({typo.mode})"))
    meaning = search_invoices(db, "milk supplier")
    results.append((meaning.mode == "hybrid" and any("meaning" in h["matched_by"] and "Dairy" in h["counterparty"] for h in meaning.hits),
                    f"by meaning: 'milk supplier' → {[h['counterparty'] for h in meaning.hits][:3]} ({meaning.mode})"))
    hl = [h for h in search_invoices(db, "oat milk").hits if h.get("highlights")]
    results.append((bool(hl) and any(p["hit"] for p in hl[0]["highlights"][0]),
                    f"highlights: 'oat milk' → {[''.join(('[' + p['value'] + ']') if p['hit'] else p['value'] for p in h['highlights'][0]) for h in hl][:2]}"))
    facets = invoice_facets(db)
    results.append((facets["mode"] == "atlas" and bool(facets["counterparty"]), f"facets ({facets['mode']}): {facets['counterparty'][:3]}"))
    sug = suggest(db, "eas")
    results.append((sug.mode == "atlas" and any(h["name"].startswith("Eastside") for h in sug.hits),
                    f"autocomplete 'eas' → {[h['name'] for h in sug.hits]} ({sug.mode})"))
    stored = db[schema.INVOICES].find_one({"business_id": BUSINESS_ID, "invoice_number": "EDC-7781"}, FIELDS)
    if stored:
        twin = {**stored, "_id": None, "invoice_number": "EDC 7781", "counterparty": "Eastside Dairy", "counterparty_key": "eastsidedairy"}
        dup = possible_duplicate(db, twin)
        results.append((bool(dup) and dup["invoice_number"] == "EDC-7781", f"duplicate check: 'EDC 7781' → {dup and dup['invoice_number']}"))
    product = find_products(db, "flat whte")
    results.append((product.mode == "atlas" and bool(product.hits) and product.hits[0]["name"] == "Flat White",
                    f"typo-tolerant product: 'flat whte' → {[h['name'] for h in product.hits][:3]} ({product.mode})"))
    acts = search_activity(db, "overdue")
    results.append((acts.mode == "atlas" and bool(acts.hits), f"activity search 'overdue': {len(acts.hits)} hit(s) ({acts.mode})"))
    notes = search_notes(db, "milk")
    results.append((notes["mode"] == "atlas", f"board memos + events 'milk': {len(notes['insights'])} insight(s), "
                                              f"{len(notes['events'])} event(s) ({notes['mode']})"))
    feed = news_feed(db)
    results.append((feed.mode == "atlas" and bool(feed.hits), f"local news ranked by soon + near: {[h['headline'][:40] for h in feed.hits][:3]} ({feed.mode})"))
    if feed.hits:
        twin = {"_id": "check", "headline": feed.hits[0]["headline"], "summary": feed.hits[0].get("summary", ""),
                "starts_at": feed.hits[0].get("starts_at"), "published_at": feed.hits[0].get("published_at")}
        found = same_story(db, twin)
        results.append((bool(found), f"moreLikeThis finds the same story: {bool(found)}"))
    mean = news_search(db, "catering work for a coffee van")
    results.append((mean.mode == "hybrid" and any("meaning" in (h.get("matched_by") or []) for h in mean.hits),
                    f"news by meaning 'catering work for a coffee van' → {[h['headline'][:35] for h in mean.hits][:3]} ({mean.mode})"))
    if feed.hits:
        like = similar_news(db, feed.hits[0]["_id"])
        results.append((like.mode == "atlas" and bool(like.hits), f"more like '{feed.hits[0]['headline'][:30]}' → "
                                                                   f"{[h['headline'][:30] for h in like.hits][:2]} ({like.mode})"))
    nf = news_facets(db)
    results.append((nf["mode"] == "atlas" and bool(nf["kind"]), f"news facets ({nf['mode']}): {nf['kind']}"))
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    fallbacks = db[schema.AGENT_LOG].count_documents({"kind": "search_fallback", "at": {"$gte": since}})
    results.append((fallbacks == 0, f"search fallbacks in the last 24 h: {fallbacks}"))
    return results


def main() -> None:
    from backend.db.client import get_db

    parser = argparse.ArgumentParser(description="Search invoices on Atlas (keyword + meaning).")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--setup", action="store_true", help="backfill search_text and create/update the indexes")
    parser.add_argument("--similar", metavar="INVOICE_NUMBER", help="similar invoices + price check")
    parser.add_argument("--check", action="store_true", help="live self-test against Atlas")
    parser.add_argument("--activity", metavar="QUERY", help="search the agent's own record")
    args = parser.parse_args()
    db = get_db()
    if args.check:
        results = live_check(db)
        for ok, what in results:
            print(f"  {'✓' if ok else '✗'} {what}")
        raise SystemExit(0 if all(ok for ok, _ in results) else 1)
    if args.setup:
        for name, status in ensure_search_indexes(db).items():
            print(f"  {name}: {status}")
    if args.activity:
        result = search_activity(db, args.activity)
        print(f"mode: {result.mode}")
        for h in result.hits:
            print(f"  {h['score']:.3f}  {h.get('at')}  {h['summary']}")
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
        for line in check.lines():
            print(f"  owner sees: {line}")
        for c in check.comparisons:
            print(f"  [{c['kind']}] {c['item']}: £{c['unit_price']:.2f} vs £{c['other_price']:.2f} at {c['counterparty']} "
                  f"({c['invoice_number']}): {c['change_pct']:+.1f}%")


if __name__ == "__main__":
    main()
