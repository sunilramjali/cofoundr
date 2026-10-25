"""More Atlas Search (Sunil, Sun 25 Oct): highlights, facets, autocomplete, the duplicate-invoice check on intake, typo-tolerant
product names, searching the agent's own record, and board memos + events. mongomock has no $search, so each test runs
the plain-match fallback on the in-memory DB, or feeds Atlas-shaped results through a fake collection."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from backend.ai.extract import ExtractedLineItem as L, Extraction
from backend.db import schema, search
from backend.pipeline import ingest_invoice

NOW = datetime(2026, 10, 25, 9, tzinfo=timezone.utc)


class Fake:
    """An Atlas collection that answers each aggregate with the next canned result (or raises it) and records pipelines."""
    def __init__(self, *answers, docs=()):
        self.answers, self.pipelines, self.docs = list(answers), [], list(docs)

    def __getitem__(self, _name):
        return self

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        answer = self.answers.pop(0) if self.answers else []
        if isinstance(answer, Exception):
            raise answer
        return iter(answer)

    def find(self, *_a, **_k):
        return Rows(self.docs)

    def find_one(self, *_a, **_k):
        return None

    def insert_one(self, *_a, **_k):
        return None

    def count_documents(self, *_a, **_k):
        return len(self.docs)

    def distinct(self, key, *_a, **_k):
        return sorted({d.get(key) for d in self.docs if d.get(key)})


class Rows(list):
    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        return Rows(self[:n])


DOWN = OperationFailure("$search is not allowed (no Atlas Search here)")


# ---------------------------------------------------------------- highlights (which words matched)

def test_atlas_highlights_become_plain_parts_best_first():
    raw = [{"score": 0.4, "texts": [{"value": "Whole milk, 2L", "type": "text"}]},
           {"score": 1.2, "texts": [{"value": "Oat ", "type": "text"}, {"value": "milk", "type": "hit"}, {"value": ", 1L", "type": "text"}]}]
    best = search._snippets(raw, limit=1)
    assert best == [[{"value": "Oat ", "hit": False}, {"value": "milk", "hit": True}, {"value": ", 1L", "hit": False}]]
    assert search._snippets(None) == [] and search._snippets([{"texts": None}]) == [[]]


def test_hybrid_search_adds_highlights_to_keyword_hits_only():
    fused = [{"_id": 1, "invoice_number": "EDC-7781", "details": {"details": [{"inputPipelineName": "keyword", "rank": 1, "value": .6}]}},
             {"_id": 2, "invoice_number": "MF-1", "details": {"details": [{"inputPipelineName": "meaning", "rank": 1, "value": .83}]}}]
    hl = [{"_id": 1, "highlights": [{"score": 1, "texts": [{"value": "Oat ", "type": "text"}, {"value": "milk", "type": "hit"}]}]}]
    atlas = Fake(fused, hl)
    hits = search.search_invoices(atlas, "milk").hits
    assert hits[0]["highlights"][0][1] == {"value": "milk", "hit": True} and "highlights" not in hits[1]
    lookup = atlas.pipelines[1]
    assert lookup[0]["$search"]["highlight"] == {"path": ["counterparty", "line_items.description"]}
    assert lookup[1] == {"$match": {"_id": {"$in": [1]}}}  # only the keyword hits are looked up


def test_highlight_failure_never_breaks_the_search():
    fused = [{"_id": 1, "invoice_number": "EDC-7781", "counterparty": "Eastside Dairy Co", "line_items": [{"description": "Oat milk, 1L"}],
              "details": {"details": [{"inputPipelineName": "keyword", "rank": 1, "value": .6}]}}]
    result = search.search_invoices(Fake(fused, DOWN), "oat milk")
    assert result.mode == "hybrid" and result.hits[0]["highlights"] == [
        [{"value": "Oat", "hit": True}, {"value": " ", "hit": False}, {"value": "milk", "hit": True}, {"value": ", ", "hit": False},
         {"value": "1L", "hit": False}]]


def test_finances_page_highlights_matches_and_escapes_untrusted_names(db):
    db[schema.INVOICES].insert_one({"business_id": "gaia-coffee", "invoice_number": "X-1", "direction": "payable", "amount": 5.0,
                                    "counterparty": "<script>alert(1)</script> Dairy", "status": "open", "line_items": [],
                                    "due_date": NOW, "currency": "GBP"})
    from bridge.main import app
    page = TestClient(app).get("/invoices", params={"q": "dairy"}).text
    assert "<mark>Dairy</mark>" in page and "<script>alert(1)" not in page and "&lt;script&gt;" in page


# ---------------------------------------------------------------- facets: invoices per supplier/customer

def test_facets_read_atlas_searchmeta_buckets():
    meta = [{"count": {"lowerBound": 16}, "facet": {
        "status": {"buckets": [{"_id": "paid", "count": 8}, {"_id": "overdue", "count": 4}]},
        "direction": {"buckets": [{"_id": "payable", "count": 11}]},
        "counterparty": {"buckets": [{"_id": "Eastside Dairy Co", "count": 5}]}}}]
    atlas = Fake(meta)
    f = search.invoice_facets(atlas)
    assert f == {"total": 16, "status": [("paid", 8), ("overdue", 4)], "direction": [("payable", 11)],
                 "counterparty": [("Eastside Dairy Co", 5)], "mode": "atlas"}
    stage = atlas.pipelines[0][0]["$searchMeta"]
    assert stage["facet"]["facets"]["counterparty"] == {"type": "string", "path": "counterparty", "numBuckets": 6}
    assert stage["facet"]["operator"] == {"equals": {"path": "business_id", "value": "gaia-coffee"}}


def test_facets_fall_back_to_counting_when_atlas_is_down_or_odd(db):
    f = search.invoice_facets(db)
    assert f["mode"] == "basic" and f["total"] == db[schema.INVOICES].count_documents({})
    assert f["counterparty"][0] == ("Eastside Dairy Co", 5) and dict(f["direction"])["receivable"] == 6
    assert search.invoice_facets(Fake([{"count": {}}], docs=[]))["mode"] == "basic"  # a malformed answer isn't trusted


def test_finances_shows_facet_chips_that_search_that_supplier(db):
    from bridge.main import app
    page = TestClient(app).get("/invoices").text
    assert 'href="/invoices?q=Eastside%20Dairy%20Co"' in page and "Atlas Search facets" not in page


# ---------------------------------------------------------------- autocomplete

def test_suggest_needs_two_letters_and_never_returns_amounts(db):
    assert search.suggest(db, "e").hits == [] and search.suggest(db, "  ").hits == [] and search.suggest(db, "%%").hits == []
    hits = search.suggest(db, "eas").hits
    assert hits == [{"name": "Eastside Dairy Co", "kind": "counterparty"}]
    assert {h["name"] for h in search.suggest(db, "dairy").hits} == {"Eastside Dairy Co"}  # any word can start the match


def test_suggest_merges_names_from_atlas_without_duplicates():
    atlas = Fake([{"counterparty": "Eastside Dairy Co"}, {"counterparty": "eastside dairy co"}], [{"name": "Espresso"}])
    hits = search.suggest(atlas, "es").hits
    assert hits == [{"name": "Eastside Dairy Co", "kind": "counterparty"}, {"name": "Espresso", "kind": "product"}]
    first = atlas.pipelines[0][0]["$search"]["compound"]["must"][0]["autocomplete"]
    assert first == {"query": "es", "path": "counterparty"}  # short prefix: no fuzziness (it would match everything)
    atlas = Fake([], [])
    search.suggest(atlas, "eastsde")
    assert atlas.pipelines[0][0]["$search"]["compound"]["must"][0]["autocomplete"]["fuzzy"] == {"maxEdits": 1, "prefixLength": 2}


def test_suggest_endpoint(db):
    from bridge.main import app
    web = TestClient(app)
    assert web.get("/search/suggest", params={"q": "hack"}).json() == {"suggestions": ["Hackney Hub Coworking"], "mode": "basic"}
    assert web.get("/search/suggest", params={"q": "x" * 500}).json()["suggestions"] == []
    assert web.get("/search/suggest").json()["suggestions"] == []


# ---------------------------------------------------------------- duplicate invoices on intake

def _bill(**k):
    return {"business_id": "gaia-coffee", "invoice_number": "EDC-7781", "direction": "payable", "counterparty": "Eastside Dairy Co",
            "counterparty_key": "eastsidedairyco", "amount": 149.6, "currency": "GBP", **k}


@pytest.mark.parametrize("change, duplicate", [
    ({"invoice_number": "EDC 7781", "counterparty": "Eastside Dairy", "counterparty_key": "eastsidedairy"}, True),
    ({"invoice_number": "edc/7781"}, True),                                                      # same supplier, odd spelling
    ({"invoice_number": "EDC-7764"}, False),                                                     # monthly bill, same amount
    ({"invoice_number": "EDC 7781", "amount": 150.6}, False),                                    # same number, other amount
    ({"invoice_number": "EDC 7781", "counterparty": "Westside Dairy", "counterparty_key": "westsidedairy"}, False),
    ({}, False),                                                                                 # exact: the unique index's job
    ({"invoice_number": "EDC 7781", "direction": "receivable"}, False),
    ({"invoice_number": "EDC 7781", "currency": "EUR"}, False),
    ({"invoice_number": "UNKNOWN-20261025"}, False),
    ({"invoice_number": ""}, False),
    ({"invoice_number": "EDC 7781", "amount": None}, False),
])
def test_is_duplicate(change, duplicate):
    other = _bill()
    if change.get("invoice_number", "").startswith("UNKNOWN"):
        other = _bill(invoice_number=change["invoice_number"], counterparty="Eastside Dairy", counterparty_key="eastsidedairy")
    assert search.is_duplicate(_bill(**change), other) is duplicate


def test_number_key():
    assert search.number_key("EDC 7781") == search.number_key("edc-7781") == search.number_key(" EDC/7781 ") == "edc7781"
    assert search.number_key(None) == ""


def test_possible_duplicate_asks_atlas_by_number_then_decides_in_code():
    stored = {**_bill(), "_id": "a"}
    atlas = Fake([stored, {**_bill(invoice_number="EDC-7764"), "_id": "b"}])
    new = _bill(invoice_number="EDC7781", counterparty="Eastside Dairy", counterparty_key="eastsidedairy")
    assert search.possible_duplicate(atlas, new)["_id"] == "a"
    compound = atlas.pipelines[0][0]["$search"]["compound"]
    assert {c["text"]["query"] for c in compound["should"]} == {"EDC7781", "EDC 7781"}  # as written + as Atlas tokenises it
    assert {"equals": {"path": "direction", "value": "payable"}} in compound["filter"]


def test_possible_duplicate_double_checks_when_atlas_finds_nothing():
    stored = {**_bill(), "_id": "a"}
    new = _bill(invoice_number="EDC 7781", counterparty="Eastside Dairy", counterparty_key="eastsidedairy")
    assert search.possible_duplicate(Fake([], docs=[stored]), new)["_id"] == "a"  # index missing/building → plain check
    assert search.possible_duplicate(Fake(DOWN, docs=[stored]), new)["_id"] == "a"
    assert search.possible_duplicate(Fake(docs=[stored]), _bill(invoice_number="UNKNOWN-1")) is None


DUPLICATE = Extraction(issuer_name="Eastside Dairy", recipient_name="GAIA Coffee", invoice_number="EDC 7781",
                       issue_date="2026-10-13", due_date="2026-10-27", printed_total=149.6,
                       line_items=[L(description="Whole milk, 2L", qty=40, unit_price=1.85),
                                   L(description="Oat milk, 1L", qty=36, unit_price=2.10)])


def test_ingest_holds_a_probable_duplicate_for_review_and_never_pays_it(db, fake_extraction):
    fake_extraction(DUPLICATE)
    original = db[schema.INVOICES].find_one({"invoice_number": "EDC-7781"})
    r = ingest_invoice(b"%PDF", "application/pdf", "email")
    assert r.ok and r.created and r.needs_review
    assert "needs your review: looks like a duplicate of EDC-7781 from Eastside Dairy Co" in r.message
    saved = db[schema.INVOICES].find_one({"invoice_number": "EDC 7781"})
    assert saved["possible_duplicate_of"] == str(original["_id"]) and saved["needs_review"]
    assert saved["verification_issues"][0].startswith("looks like a duplicate")
    assert not db[schema.APPROVALS].find_one({"target_id": str(saved["_id"])})  # held: never chased or paid twice


def test_ingest_of_a_new_monthly_bill_is_not_a_duplicate(db, fake_extraction):
    fake_extraction(DUPLICATE.model_copy(update={"invoice_number": "EDC-7799", "issuer_name": "Eastside Dairy Co"}))
    r = ingest_invoice(b"%PDF", "application/pdf", "email")
    assert r.created and not r.needs_review and "duplicate" not in r.message


def test_a_broken_duplicate_check_never_stops_an_invoice(db, fake_extraction, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("search down")
    monkeypatch.setattr(search, "possible_duplicate", boom)
    fake_extraction(DUPLICATE)
    r = ingest_invoice(b"%PDF", "application/pdf", "email")
    assert r.created and "duplicate" not in r.message
    assert db[schema.AGENT_LOG].find_one({"kind": "duplicate_check_failed"})


# ---------------------------------------------------------------- product names, typo-tolerant

MENU = [{"_id": "FLW", "name": "Flat White", "price": 3.9}, {"_id": "MAT", "name": "Hot Matcha", "price": 4.5},
        {"_id": "IMA", "name": "Iced Matcha", "price": 5.0}, {"_id": "LAT", "name": "Latte", "price": 3.9}]


def test_pick_product_only_when_the_match_is_clear():
    atlas = lambda *hits: search.SearchResult([{"name": n, "score": s} for n, s in hits], "atlas")  # noqa: E731
    assert search.pick_product("latte", atlas(("Iced Latte", 9), ("Latte", 3)))[0]["name"] == "Latte"  # exact wins
    assert search.pick_product("flat whte", atlas(("Flat White", 4.0), ("Latte", 1.0)))[0]["name"] == "Flat White"
    assert search.pick_product("matcha", atlas(("Hot Matcha", 2.0), ("Iced Matcha", 2.0))) == (None, ["Hot Matcha", "Iced Matcha"])
    assert search.pick_product("x", atlas()) == (None, [])
    basic = search.SearchResult([{"name": "Hot Matcha", "score": 1}, {"name": "Iced Matcha", "score": 1}], "basic")
    assert search.pick_product("matcha", basic)[0] is None  # a plain match never guesses between two


def test_find_products_pipeline_and_fallbacks(db):
    atlas = Fake([{"_id": "FLW", "name": "Flat White", "price": 3.9, "score": 4.2}])
    assert search.find_products(atlas, "flat whte").hits[0]["name"] == "Flat White"
    should = atlas.pipelines[0][0]["$search"]["compound"]["should"]
    assert {"autocomplete": {"query": "flat whte", "path": "name"}} in should
    assert any(c.get("text", {}).get("fuzzy") == {"maxEdits": 1} for c in should)
    silent = Fake([], docs=MENU)
    assert search.find_products(silent, "flat").mode == "basic"  # Atlas silent, a plain match finds it: index trouble
    off_menu = search.find_products(Fake([], docs=MENU), "croissant")
    assert off_menu.mode == "atlas" and off_menu.hits == []  # simply not on the menu: no false "Atlas unavailable" alarm
    db[schema.PRODUCTS].insert_many([{**p, "business_id": "gaia-coffee"} for p in MENU])
    assert [h["name"] for h in search.find_products(db, "matcha").hits] == ["Hot Matcha", "Iced Matcha"]
    assert search.find_products(db, "  ").hits == []


def test_what_if_takes_a_typo_as_the_product_atlas_found(db, monkeypatch):
    from backend.outputs import assistant
    db[schema.PRODUCTS].insert_many([{**p, "business_id": "gaia-coffee"} for p in MENU])
    monkeypatch.setattr(search, "find_products", lambda _db, _q: search.SearchResult(
        [{"_id": "FLW", "name": "Flat White", "price": 3.9, "score": 5.0}, {"_id": "LAT", "name": "Latte", "price": 3.9, "score": 1.0}], "atlas"))
    fns = {f.__name__: f for f in assistant.tools(lambda _n, r: r)}
    r = fns["what_if_price"]("flat whte", 20)
    assert r["found"] and r["product"] == "Flat White" and r["read_as"] == "took “flat whte” as Flat White"
    assert "read_as" not in fns["what_if_price"]("Flat White", 20)
    monkeypatch.setattr(search, "find_products", lambda _db, _q: search.SearchResult(
        [{"_id": "MAT", "name": "Hot Matcha", "score": 2.0}, {"_id": "IMA", "name": "Iced Matcha", "score": 2.0}], "atlas"))
    assert fns["what_if_price"]("matcha", 20)["did_you_mean"] == ["Hot Matcha", "Iced Matcha"]


# ---------------------------------------------------------------- the agent's own record

def _log(db, summary, kind="overdue_check", actor="agent"):
    db[schema.AGENT_LOG].insert_one({"business_id": "gaia-coffee", "kind": kind, "summary": summary, "actor": actor, "at": NOW})


def test_search_activity_plain_match_with_highlights(db):
    _log(db, "#4 drafted: Chase Hackney Hub Coworking £450.00")
    _log(db, "#4 approved via whatsapp: Chase Hackney Hub Coworking", kind="approval_decided", actor="owner")
    _log(db, "Read EDC-7781 from Eastside Dairy Co")
    r = search.search_activity(db, "hackney")
    assert r.mode == "basic" and len(r.hits) == 2 and {h["actor"] for h in r.hits} == {"agent", "owner"}
    assert {"value": "Hackney", "hit": True} in r.hits[0]["highlights"][0]
    assert search.search_activity(db, "?!").hits == [] and search.search_activity(db, "zebra").hits == []


def test_search_activity_reads_atlas_scores_and_highlights():
    atlas = Fake([{"summary": "Chase Hackney Hub", "kind": "overdue_check", "score": 3.1,
                   "highlights": [{"score": 1, "texts": [{"value": "Chase ", "type": "text"}, {"value": "Hackney", "type": "hit"}]}]}])
    r = search.search_activity(atlas, "hackney")
    assert r.mode == "atlas" and r.hits[0]["highlights"] == [[{"value": "Chase ", "hit": False}, {"value": "Hackney", "hit": True}]]
    assert atlas.pipelines[0][0]["$search"]["index"] == search.ACTIVITY_INDEX


def test_search_activity_double_checks_a_silent_atlas():
    log = [{"summary": "Chase Hackney Hub", "kind": "overdue_check", "at": NOW}]
    assert search.search_activity(Fake([], docs=log), "hackney").mode == "basic"


def test_activity_page_searches_and_still_shows_the_timeline(db):
    from bridge.main import app
    _log(db, "Read GAIA-1041 for Hackney Hub Coworking")
    web = TestClient(app)
    found = web.get("/activity", params={"q": "hackney"}).text
    assert "<mark>Hackney</mark>" in found and "1 step matching" in found and "Back to everything" in found
    assert "Search what I did" in web.get("/activity").text


def test_assistant_can_search_its_own_record(db):
    from backend.outputs import assistant
    _log(db, "Read GAIA-1041 for Hackney Hub Coworking")
    fns = {f.__name__: f for f in assistant.tools(lambda _n, r: r)}
    out = fns["search_activity"]("Hackney")
    assert out["events"][0]["what"] == "Read GAIA-1041 for Hackney Hub Coworking" and out["events"][0]["by"] == "agent"


# ---------------------------------------------------------------- board memos and events

MEMO = {"_id": "gaia-coffee:2026-10-24", "business_id": "gaia-coffee", "date": "2026-10-24", "headline": "Cash is stuck",
        "insights": [{"title": "Oat milk price creeping up", "finding": "Eastside raised oat milk 8%.", "action": "Ask for a match.",
                      "status": "verified"},
                     {"title": "Pastries", "finding": "We sold 8 Fresh Pastries.", "action": "Bake less.", "status": "corrected"}]}


def test_search_notes_returns_only_the_matching_insights_and_live_events(db):
    db[schema.BOARD_MEMOS].insert_one(MEMO)
    db[schema.EVENTS].insert_many([
        {"business_id": "gaia-coffee", "name": "Broadway Market", "venue": "Broadway Market", "date": "2026-10-31", "status": "onsale"},
        {"business_id": "gaia-coffee", "name": "Cancelled market", "venue": "London Fields", "date": "2026-10-31", "status": "removed"}])
    found = search.search_notes(db, "oat milk")
    assert found["mode"] == "basic" and [i["title"] for i in found["insights"]] == ["Oat milk price creeping up"]
    assert found["insights"][0]["date"] == "2026-10-24" and found["events"] == []
    assert [e["name"] for e in search.search_notes(db, "market")["events"]] == ["Broadway Market"]
    assert search.search_notes(db, "") == {"insights": [], "events": [], "mode": "atlas"}


def test_search_notes_on_atlas_queries_both_indexes():
    atlas = Fake([MEMO], [{"name": "Halloween Market", "venue": "London Fields", "date": "2026-10-31"}])
    found = search.search_notes(atlas, "pastries")
    assert found["mode"] == "atlas" and [i["title"] for i in found["insights"]] == ["Pastries"]
    assert [p[0]["$search"]["index"] for p in atlas.pipelines] == [search.MEMOS_INDEX, search.EVENTS_INDEX]
    assert atlas.pipelines[1][0]["$search"]["compound"]["mustNot"] == [{"equals": {"path": "status", "value": "removed"}}]


# ---------------------------------------------------------------- creating the indexes

class IndexAdmin:
    """Atlas index admin for one database: refuses the named indexes (e.g. a cluster's index limit)."""
    def __init__(self, refuse=(), collections=None):
        self.refuse, self.made, self.created = set(refuse), {}, []
        self.collections = set(collections if collections is not None else
                               {c for c, _k, _d in search.INDEXES.values()})

    def list_collection_names(self):
        return sorted(self.collections)

    def create_collection(self, name):
        self.collections.add(name)
        self.created.append(name)

    def __getitem__(self, coll):
        admin = self

        class Coll:
            def find(self, *_a, **_k):
                return []

            def list_search_indexes(self):
                class Cursor(list):
                    def try_next(self):
                        return None
                return Cursor({"name": n, "status": "BUILDING"} for n, c in admin.made.items() if c == coll)

            def create_search_index(self, model):
                name = model.document["name"]
                if name in admin.refuse:
                    raise OperationFailure("You have reached the maximum number of search indexes")
                admin.made[name] = coll
        return Coll()


def test_indexes_are_created_one_by_one_and_a_refusal_never_stops_the_rest():
    admin = IndexAdmin(refuse={search.MEMOS_INDEX})
    out = search.ensure_search_indexes(admin)
    assert out[search.MEMOS_INDEX].startswith("refused: You have reached the maximum")
    assert {n: s for n, s in out.items() if n != search.MEMOS_INDEX} == {
        n: "BUILDING" for n in search.INDEXES if n != search.MEMOS_INDEX}
    assert admin.made[search.PRODUCTS_INDEX] == schema.PRODUCTS and admin.made[search.ACTIVITY_INDEX] == schema.AGENT_LOG


def test_most_useful_indexes_come_first():
    assert list(search.INDEXES)[:4] == [search.TEXT_INDEX, search.SEMANTIC_INDEX, search.PRODUCTS_INDEX, search.ACTIVITY_INDEX]


def test_setup_creates_a_missing_collection_before_indexing_it():
    admin = IndexAdmin(collections={schema.INVOICES, schema.PRODUCTS, schema.AGENT_LOG, schema.BOARD_MEMOS, schema.EVENTS})
    out = search.ensure_search_indexes(admin)
    assert admin.created == [schema.NEWS, schema.MEMORIES] and out[search.NEWS_INDEX] == "BUILDING"
