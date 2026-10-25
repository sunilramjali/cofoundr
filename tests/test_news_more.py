"""Local news, round 2 (Sunil, Sun 25 Oct): TfL disruptions in the feed, links the owner adds (web page, event page,
TikTok via official oEmbed, Instagram with a note), safely (no SSRF, robots.txt respected), and news searched by meaning
(Vector Search + Automated Embedding), hybrid ($rankFusion) and "more like this one". No network."""
import socket
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import news
from backend.db import schema, search
from backend.inputs import link_preview
from backend.inputs.link_preview import LinkRefused
from tests.test_search_more import Fake

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.date().isoformat()


def _resolver(*ips):
    return lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


# ---------------------------------------------------------------- no server-side request forgery

@pytest.mark.parametrize("url, ips, why", [
    ("javascript:alert(1)", ("93.184.216.34",), "isn't a web link"),
    ("ftp://example.com/x", ("93.184.216.34",), "isn't a web link"),
    ("https://" + "a" * 2000 + ".com", ("93.184.216.34",), "isn't a web link"),
    ("https://user:pw@example.com/", ("93.184.216.34",), "username or password"),
    ("https://example.com:8080/", ("93.184.216.34",), "unusual ports"),
    ("http://localhost/admin", ("127.0.0.1",), "private network"),
    ("http://metadata.example/", ("169.254.169.254",), "private network"),   # cloud metadata
    ("http://intranet.example/", ("10.0.0.5",), "private network"),
    ("http://v6.example/", ("::1",), "private network"),
    ("http://mixed.example/", ("93.184.216.34", "192.168.1.1"), "private network"),  # one private address is enough
])
def test_check_url_refuses_anything_unsafe(url, ips, why):
    with pytest.raises(LinkRefused, match=why):
        link_preview.check_url(url, resolve=_resolver(*ips))


def test_check_url_accepts_a_public_page_and_unknown_hosts_are_refused():
    assert link_preview.check_url("https://example.com/a?b=1", resolve=_resolver("93.184.216.34")) == "https://example.com/a?b=1"

    def missing(*_a):
        raise socket.gaierror("no such host")
    with pytest.raises(LinkRefused, match="couldn't be found"):
        link_preview.check_url("https://nope.invalid/", resolve=missing)


def test_a_redirect_into_the_private_network_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("127.0.0.1"))
    with pytest.raises(LinkRefused, match="private network"):
        link_preview._SafeRedirects().redirect_request(None, None, 302, "Found", {}, "http://localhost/secret")


# ---------------------------------------------------------------- reading one page, politely

PAGE = """<html><head><title>Fallback title</title>
<meta content="Halloween Night Market &amp; Food Fair" property="og:title">
<meta property="og:description" content="Street food and coffee at London Fields, Sat 31 Oct.">
<meta property="og:site_name" content="Hackney Events"></head><body>…</body></html>"""


def test_parse_page_reads_open_graph_in_either_attribute_order_and_falls_back_to_title():
    assert link_preview.parse_page(PAGE) == {"headline": "Halloween Night Market & Food Fair",
                                             "summary": "Street food and coffee at London Fields, Sat 31 Oct.", "site": "Hackney Events"}
    assert link_preview.parse_page("<title> Just a <b>title</b> </title>")["headline"] == "Just a title"
    assert link_preview.parse_page("<p>nothing</p>") == {"headline": "", "summary": "", "site": ""}


@pytest.fixture
def web(monkeypatch):
    """Fake internet: public DNS; pages by url; robots.txt per host."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
    pages = {}

    def get(url, _accept):
        link_preview.check_url(url)
        if url not in pages:
            raise LinkRefused("The page answered with an error (404).")
        return pages[url].encode()
    monkeypatch.setattr(link_preview, "_get", get)
    return pages


def test_read_a_web_page_only_when_robots_allows_it(web):
    web["https://events.example/halloween"] = PAGE
    out = link_preview.read("https://events.example/halloween")
    assert out == {"url": "https://events.example/halloween", "headline": "Halloween Night Market & Food Fair",
                   "summary": "Street food and coffee at London Fields, Sat 31 Oct.", "source": "Owner's link · Hackney Events"}
    web["https://events.example/robots.txt"] = "User-agent: *\nDisallow: /"
    with pytest.raises(LinkRefused, match="robots.txt"):
        link_preview.read("https://events.example/halloween")
    web["https://events.example/robots.txt"] = "User-agent: *\nDisallow: /private"
    assert link_preview.read("https://events.example/halloween", note="Ask about a pitch")["summary"] == "Ask about a pitch"


def test_a_page_without_a_title_needs_a_note(web):
    web["https://bare.example/"] = "<p>no title</p>"
    with pytest.raises(LinkRefused, match="add a short note"):
        link_preview.read("https://bare.example/")
    assert link_preview.read("https://bare.example/", note="Coworking opening in E8")["headline"] == "Coworking opening in E8"


def test_tiktok_goes_through_the_official_oembed_only(web):
    video = "https://www.tiktok.com/@hackneyeats/video/123"
    web[f"{link_preview.TIKTOK_OEMBED}?url=https%3A%2F%2Fwww.tiktok.com%2F%40hackneyeats%2Fvideo%2F123"] = \
        '{"title": "Best coffee trucks at Broadway Market", "author_name": "hackneyeats"}'
    out = link_preview.read(video)
    assert out["headline"] == "Best coffee trucks at Broadway Market" and out["summary"] == "TikTok video by hackneyeats"
    assert out["source"] == "Owner's link · TikTok"
    with pytest.raises(LinkRefused, match="try again later, or add a short note"):
        link_preview.read("https://www.tiktok.com/@x/video/999")  # oEmbed didn't answer: nothing guessed
    noted = link_preview.read("https://www.tiktok.com/@x/video/999", note="Food truck meetup at Victoria Park")
    assert noted["headline"] == "Food truck meetup at Victoria Park" and noted["source"] == "Owner's link · TikTok"


def test_instagram_needs_the_owners_note_and_is_never_fetched(web):
    with pytest.raises(LinkRefused, match="Meta's API"):
        link_preview.read("https://www.instagram.com/p/abc/")
    out = link_preview.read("https://www.instagram.com/p/abc/", note="Pop-up market at Netil House this Sunday")
    assert out["source"] == "Owner's link · Instagram" and out["headline"].startswith("Pop-up market")
    assert not any("instagram" in u for u in web)  # nothing was requested from Instagram


def test_add_link_stores_it_logs_it_and_explains_refusals(db, web, monkeypatch):
    web["https://events.example/halloween"] = PAGE
    r = news.add_link("https://events.example/halloween", "event", when=(NOW + timedelta(days=3)).date().isoformat(), now=NOW)
    assert r["ok"] and "Halloween Night Market" in r["message"]
    doc = db[schema.NEWS].find_one({"_id": r["id"]})
    assert doc["kind"] == "event" and doc["starts_at"].date() == (NOW + timedelta(days=3)).date() and doc["source"] == "Owner's link · Hackney Events"
    assert db[schema.AGENT_LOG].find_one({"kind": "news_link_added", "actor": "owner"})
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("127.0.0.1"))  # what real DNS says for localhost
    no = news.add_link("http://localhost/", now=NOW)
    assert not no["ok"] and "private network" in no["message"] and db[schema.AGENT_LOG].find_one({"kind": "news_link_refused"})
    assert not news.add_link("https://events.example/halloween", "gossip", now=NOW)["ok"]


def test_add_link_route_is_post_only_rate_limited_and_ignores_a_bad_date(db, web, monkeypatch):
    from bridge.main import app
    web["https://events.example/halloween"] = PAGE
    client = TestClient(app)
    first = client.post("/news/add", data={"url": "https://events.example/halloween", "kind": "event", "when": "31/10"},
                        follow_redirects=False)
    assert first.status_code == 303 and "Added" in first.headers["location"]
    assert db[schema.NEWS].find_one({"url": "https://events.example/halloween"})["kind"] == "news"  # no valid date: not an event
    again = client.post("/news/add", data={"url": "https://events.example/other"}, follow_redirects=False)
    assert "One%20link%20at%20a%20time" in again.headers["location"]
    assert client.get("/news/add").status_code == 405


# ---------------------------------------------------------------- TfL disruptions as news

def _disruption(db, dates, state="active", sid="s1", line="central"):
    db[schema.TRANSPORT].insert_one({"business_id": "gaia-coffee", "source": "tfl", "source_id": sid, "line_id": line,
                                     "line_name": line.title(), "status": "Severe Delays", "impact": "delays", "reason": "Train cancellations.",
                                     "dates": dates, "stations": ["Bethnal Green"], "nearest_m": 1048, "state": state,
                                     "fetched_at": NOW, "last_seen_at": NOW})


def test_transport_takes_only_upcoming_active_disruptions_one_item_each(db):
    yesterday, tomorrow = (NOW - timedelta(days=1)).date().isoformat(), (NOW + timedelta(days=1)).date().isoformat()
    _disruption(db, [yesterday, TODAY, tomorrow])
    _disruption(db, [tomorrow], sid="s2", line="overground")
    _disruption(db, [yesterday], sid="s3")                     # over
    _disruption(db, [tomorrow], state="resolved", sid="s4")    # resolved
    items = news.from_transport(NOW)
    assert [i["headline"] for i in items] == ["Central: severe delays at Bethnal Green", "Overground: severe delays at Bethnal Green"]
    assert len({i["url"] for i in items}) == 2 and items[0]["source"] == news.TFL_SOURCE
    doc = news.to_doc(items[0], NOW)
    assert doc["kind"] == "transport" and doc["distance_km"] == 1.05
    assert doc["starts_at"].date().isoformat() == TODAY and doc["expires_at"].date().isoformat() == (NOW + timedelta(days=2)).date().isoformat()


def test_news_page_credits_tfl(db):
    from bridge.main import app
    _disruption(db, [TODAY])
    news.record_news(news.from_transport(NOW), NOW)
    page = TestClient(app).get("/news").text
    assert "Central: severe delays at Bethnal Green" in page and "Powered by TfL Open Data" in page


def test_a_changed_summary_is_re_embedded(db):
    item = {"url": "https://e/a", "headline": "Market", "summary": "Old line", "source": "T", "published_at": NOW}
    news.record_news([item], NOW)
    news.record_news([{**item, "summary": "New line"}], NOW)
    assert db[schema.NEWS].find_one()["search_text"] == "Market | New line"


# ---------------------------------------------------------------- by meaning, hybrid, more like this

def _fused(_id, headline, keyword=None, meaning=None, **k):
    details = [{"inputPipelineName": "keyword", "rank": 1 if keyword else 0, **({"value": keyword} if keyword else {})},
               {"inputPipelineName": "meaning", "rank": 1 if meaning else 0, **({"value": meaning} if meaning else {})}]
    return {"_id": _id, "headline": headline, "kind": "opportunity", "details": {"details": details}, **k}


def test_news_search_is_hybrid_and_drops_weak_meaning_and_duplicates():
    atlas = Fake([_fused("a", "Film shoots hire craft baristas", meaning=0.81),
                  _fused("b", "Coffee carts get event quote requests", meaning=0.78),
                  _fused("c", "Copy of the film story", meaning=0.80, duplicate_of="a"),
                  _fused("d", "Council budget cuts", meaning=0.66)], [])
    r = search.news_search(atlas, "catering work", now=NOW)
    assert r.mode == "hybrid" and [h["_id"] for h in r.hits] == ["a", "b"]
    fusion = atlas.pipelines[0][0]["$rankFusion"]["input"]["pipelines"]
    vector = fusion["meaning"][0]["$vectorSearch"]
    assert vector["index"] == search.NEWS_SEMANTIC and vector["query"] == {"text": "catering work"}  # Atlas embeds the query
    assert vector["filter"] == {"business_id": "gaia-coffee", "expires_at": {"$gte": NOW}}
    assert fusion["keyword"][0]["$search"]["compound"]["mustNot"] == [{"exists": {"path": "duplicate_of"}}]


def test_news_search_falls_back_to_keywords_then_plain(db):
    news.record_news([{"url": "https://e/m", "headline": "Night market returns", "summary": "", "source": "T", "published_at": NOW}], NOW)
    r = search.news_search(db, "market", now=NOW)  # mongomock: no $rankFusion → keyword feed → plain match
    assert r.mode == "basic" and r.hits[0]["headline"] == "Night market returns"
    assert search.news_search(db, "  ", now=NOW).mode in ("basic", "atlas")  # no query: the ranked feed


class AtlasWithMe(Fake):
    def __init__(self, me, *answers, docs=()):
        super().__init__(*answers, docs=docs)
        self.me = me

    def find_one(self, *_a, **_k):
        return self.me


def test_more_like_this_one_uses_vector_search_and_never_returns_itself():
    me = {"_id": "a", "headline": "Film shoots hire craft baristas", "search_text": "Film shoots hire craft baristas | early coffee"}
    atlas = AtlasWithMe(me, [{"_id": "a", "headline": "itself", "score": 1.0}, {"_id": "b", "headline": "Coffee carts quotes", "score": 0.8},
                             {"_id": "c", "headline": "dup", "score": 0.79, "duplicate_of": "b"}])
    r = search.similar_news(atlas, "a", now=NOW)
    assert r.mode == "atlas" and [h["_id"] for h in r.hits] == ["b"]
    assert atlas.pipelines[0][0]["$vectorSearch"]["query"] == {"text": me["search_text"]}
    assert search.similar_news(AtlasWithMe(None), "zzz", now=NOW).hits == []


def test_more_like_this_falls_back_to_shared_words(db):
    for url, h in (("https://e/1", "Coffee carts at weddings"), ("https://e/2", "Coffee carts at film shoots"), ("https://e/3", "Council cuts")):
        news.record_news([{"url": url, "headline": h, "summary": "", "source": "T", "published_at": NOW}], NOW)
    r = search.similar_news(db, news.news_id("https://e/1"), now=NOW)
    assert r.mode == "basic" and [h["headline"] for h in r.hits] == ["Coffee carts at film shoots"]


def test_news_page_more_like_this_and_unknown_ids(db):
    from bridge.main import app
    for url, h in (("https://e/1", "Coffee carts at weddings"), ("https://e/2", "Coffee carts at film shoots")):
        news.record_news([{"url": url, "headline": h, "summary": "", "source": "T", "published_at": NOW}], NOW)
    client = TestClient(app)
    page = client.get("/news", params={"like": news.news_id("https://e/1")}).text
    assert "More like “Coffee carts at weddings”" in page and "Coffee carts at film shoots" in page
    assert "Soonest and closest first" in client.get("/news", params={"like": "nope"}).text
    assert "More like this" in client.get("/news").text and "Add something you found" in client.get("/news").text


def test_assistant_news_search_by_meaning(db, monkeypatch):
    from backend.outputs import assistant
    called = []
    monkeypatch.setattr(search, "news_search", lambda _db, q, **_k: called.append(q) or search.SearchResult([], "hybrid"))
    fns = {f.__name__: f for f in assistant.tools(lambda _n, r: r)}
    fns["local_news"]("catering work")
    fns["local_news"]()
    assert called == ["catering work"]  # a query → hybrid; no query → the ranked feed


def test_vector_index_for_news_filters_what_the_queries_filter():
    coll, kind, definition = search.INDEXES[search.NEWS_SEMANTIC]
    paths = {f["path"]: f["type"] for f in definition["fields"]}
    assert coll == schema.NEWS and kind == "vectorSearch" and paths["search_text"] == "autoEmbed"
    assert {"business_id", "kind", "expires_at"} <= {p for p, t in paths.items() if t == "filter"}
    assert definition["fields"][0]["model"] == search.EMBEDDING_MODEL
