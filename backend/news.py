"""Local news for the owner: events, markets, venues and openings near the pitches. Awareness only, like a news feed:
nothing here contacts anyone or changes the business's data.

Sources → `news` (one doc per link, idempotent):
- local RSS feeds (backend/inputs/news_feeds.py), filtered by Gemini to what matters to a coffee truck (backend/ai/news.py)
- events near the pitches already stored by the events connector (Ticketmaster)
- TfL disruptions at stations near the pitch (already stored by the transport connector; "Powered by TfL Open Data")
- the owner's own scouting notes (data/news_notes.json): each one a public fact with its source link
- links the owner adds on /news (a web page, an event page, a TikTok video via TikTok's official oEmbed, or an
  Instagram post with the owner's own note): read once, politely (robots.txt), never scraped or searched
The same story from two sources is stored once (Atlas Search moreLikeThis + a date check, backend/db/search.py).
Only recent news (the last 7 days) and upcoming events: everything else expires. The feed ranks by how soon and how
close (Atlas Search `near` on date + distance).

Usage (from the repo root):
    python -m backend.news --refresh            # feeds + events + notes → news
    python -m backend.news --refresh --dry-run  # print what would be stored
    python -m backend.news                      # print the feed as the owner sees it
"""
import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from backend.db import activity, schema, search
from backend.db.client import get_db
from backend.inputs.pitches import PITCHES

BUSINESS_ID = "gaia-coffee"
NOTES_FILE = Path(__file__).resolve().parent.parent / "data" / "news_notes.json"
KINDS = ("event", "market", "venue", "opportunity", "transport", "news")
TFL_STATUS = "https://tfl.gov.uk/tube-dlr-overground/status/"
TFL_SOURCE = "TfL (Powered by TfL Open Data)"
RECENT = timedelta(days=7)  # only recent news: older stories aren't stored, stored ones drop out after this
# Only upcoming events: an event counts from today on and drops out when its (UTC) day ends
MAX_HEADLINE, MAX_SUMMARY = 300, 200
FIELDS = ("kind", "headline", "summary", "url", "source", "published_at", "starts_at", "location", "distance_km", "expires_at",
          "search_text")  # search_text too: a changed summary must be re-embedded, never left stale


def news_id(url: str) -> str:
    return f"{BUSINESS_ID}:{hashlib.sha1(url.strip().encode()).hexdigest()[:20]}"


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2, dp, dl = map(math.radians, (lat1, lat2, lat2 - lat1, lon2 - lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _aware(value) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def to_doc(item: dict, now: datetime) -> dict:
    """One source item → a `news` doc (schema.NewsItem). ValueError for anything not safe or not usable."""
    url = (item.get("url") or "").strip()
    headline = re.sub(r"\s+", " ", item.get("headline") or "").strip()[:MAX_HEADLINE]
    if not re.match(r"https?://[^\s<>\"']+$", url):
        raise ValueError("no http(s) link")
    if not headline:
        raise ValueError("no headline")
    kind = item.get("kind") or "news"
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    published = _aware(item.get("published_at")) or now
    if published < now - RECENT:
        raise ValueError("older than a week")
    starts = _aware(item.get("starts_at"))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if starts and starts < today:  # an article about something already over is news, not an upcoming event
        starts, kind = None, ("news" if kind == "event" else kind)
    if kind in ("event", "transport") and not starts:  # only upcoming: no date (or one already over) is plain news
        kind = "news"
    summary = re.sub(r"\s+", " ", item.get("summary") or "").strip()
    doc = {"_id": news_id(url), "business_id": BUSINESS_ID, "kind": kind, "headline": headline,
           "summary": summary if len(summary) <= MAX_SUMMARY else summary[:MAX_SUMMARY].rsplit(" ", 1)[0] + "…",
           "url": url, "source": (item.get("source") or "Unknown source")[:80], "published_at": published,
           "synthetic": False, "relevance": item.get("relevance")}
    if starts:
        doc["starts_at"] = starts
    lat, lon = item.get("lat"), item.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180:
        pitch = PITCHES[0]
        doc["location"] = {"type": "Point", "coordinates": [float(lon), float(lat)]}
        doc["distance_km"] = round(_km(pitch["lat"], pitch["lon"], lat, lon), 2)
    elif isinstance(item.get("distance_km"), (int, float)) and item["distance_km"] >= 0:  # e.g. TfL: nearest station
        doc["distance_km"] = round(float(item["distance_km"]), 2)
    until = _aware(item.get("until")) if starts else None  # a disruption or event over several days: its last day
    last = max(starts, until) if until else starts
    doc["expires_at"] = (last.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) if last
                         else published + RECENT)
    doc["search_text"] = f"{doc['headline']} | {doc['summary']}"  # what Atlas Search + Automated Embedding index
    return doc


def record_news(items: Iterable[dict], now: Optional[datetime] = None) -> dict:
    """Store items once per link: new ones inserted (marked `duplicate_of` when another source already has the story),
    known ones refreshed. Returns {"new", "updated", "duplicates", "skipped": [reasons]}."""
    db, now = get_db(), now or datetime.now(timezone.utc)
    out = {"new": 0, "updated": 0, "duplicates": 0, "skipped": []}
    for item in items:
        try:
            doc = to_doc(item, now)
        except ValueError as e:
            out["skipped"].append(f"{(item.get('headline') or '?')[:40]}: {e}")
            continue
        coll = db[schema.NEWS]
        if coll.find_one({"_id": doc["_id"]}, {"_id": 1}):
            coll.update_one({"_id": doc["_id"]}, {"$set": {k: doc[k] for k in FIELDS if k in doc}})
            out["updated"] += 1
            continue
        twin = search.same_story(db, doc)
        if twin:
            doc["duplicate_of"] = twin["_id"]
            out["duplicates"] += 1
        coll.insert_one({**doc, "fetched_at": now})
        out["new"] += 1
    return out


def merge_duplicates() -> int:
    """Mark later copies of a story told by an earlier item (e.g. stored while the search index was still building).
    The earliest item stays; returns how many were marked."""
    db = get_db()
    marked = 0
    for d in db[schema.NEWS].find({"business_id": BUSINESS_ID, "duplicate_of": {"$exists": False}}).sort([("published_at", 1), ("_id", 1)]):
        twin = search.same_story(db, d)
        if twin and (search._aware(twin["published_at"]), twin["_id"]) < (search._aware(d["published_at"]), d["_id"]):
            db[schema.NEWS].update_one({"_id": d["_id"]}, {"$set": {"duplicate_of": twin["_id"]}})
            marked += 1
    return marked


def from_events(now: datetime) -> List[dict]:
    """Events near the pitches (already stored by the events connector) as news items."""
    db = get_db()
    out = []
    for e in db[schema.EVENTS].find({"business_id": BUSINESS_ID, "status": {"$ne": "removed"}, "date": {"$gte": now.date().isoformat()}}):
        starts = _aware(e.get("starts_at")) or _aware(f"{e.get('date')}T12:00:00+00:00")
        where = ", ".join(p for p in (e.get("venue"), f"{e['distance_km']:.1f} km from the truck" if e.get("distance_km") is not None else None) if p)
        out.append({"kind": "event", "headline": e.get("name"), "url": e.get("url") or "", "source": (e.get("source") or "events").title(),
                    "summary": where, "published_at": e.get("fetched_at"), "starts_at": starts, "lat": e.get("lat"), "lon": e.get("lon")})
    return out


def from_transport(now: datetime) -> List[dict]:
    """Active TfL disruptions at stations near the pitch, today or later, as news items (one per disruption)."""
    db = get_db()
    today = now.date().isoformat()
    out = []
    for t in db[schema.TRANSPORT].find({"business_id": BUSINESS_ID, "state": "active", "dates": {"$elemMatch": {"$gte": today}}}):
        days = [d for d in t.get("dates") or [] if d >= today]
        stations = ", ".join(t.get("stations") or [])
        out.append({"kind": "transport", "headline": f"{t.get('line_name') or t.get('line_id')}: {(t.get('status') or 'disruption').lower()} at {stations}",
                    "summary": t.get("reason") or "", "url": f"{TFL_STATUS}#{t.get('line_id')}-{t.get('source_id')}",
                    "source": TFL_SOURCE, "published_at": t.get("last_seen_at") or t.get("fetched_at"),
                    "starts_at": f"{days[0]}T00:00:00+00:00", "until": f"{days[-1]}T00:00:00+00:00",
                    "distance_km": (t.get("nearest_m") or 0) / 1000})
    return out


def add_link(url: str, kind: str = "opportunity", note: str = "", when: str = "", now: Optional[datetime] = None) -> dict:
    """The owner adds one link to their news (awareness only). Read once by backend/inputs/link_preview.py.
    Returns {"ok", "message", "id"}; never raises."""
    from backend.inputs import link_preview

    now = now or datetime.now(timezone.utc)
    if kind not in KINDS:
        return {"ok": False, "message": f"Pick one of: {', '.join(KINDS)}."}
    try:
        page = link_preview.read(url, note=note)
    except link_preview.LinkRefused as e:
        activity.log("news_link_refused", f"Didn't add a link: {e}", actor="owner")
        return {"ok": False, "message": str(e)}
    item = {**page, "kind": kind, "published_at": now}
    if when:
        item["starts_at"] = f"{when}T00:00:00+00:00"
    stored = record_news([item], now)
    if stored["skipped"]:
        return {"ok": False, "message": f"Couldn't add it: {stored['skipped'][0].split(': ', 1)[-1]}."}
    doc_id = news_id(page["url"])
    activity.log("news_link_added", f"Added to your news: {page['headline'][:80]} ({page['source']})", actor="owner", news_id=doc_id)
    return {"ok": True, "message": f"Added “{page['headline'][:80]}” to your news.", "id": doc_id}


def load_notes(path: Path = NOTES_FILE) -> List[dict]:
    """The owner's scouting notes: public facts with a source link each, dated when written (they age out like news)."""
    if not path.exists():
        return []
    notes = json.loads(path.read_text())
    return [{"source": "Owner's notes", **n} for n in notes if isinstance(n, dict)]


def refresh(now: Optional[datetime] = None, dry_run: bool = False) -> dict:
    """Feeds (Gemini-filtered) + events + notes → news. Never raises; every outcome is logged."""
    from backend.ai import news as ai_news
    from backend.inputs import news_feeds

    now = now or datetime.now(timezone.utc)
    db = get_db()
    fetched, errors = news_feeds.fetch_all()
    # Ask Gemini only about recent links we don't have yet (older ones couldn't be stored anyway)
    fresh = [i for i in fetched if _aware(i.get("published_at")) and _aware(i["published_at"]) >= now - RECENT
             and not db[schema.NEWS].find_one({"_id": news_id(i["url"])}, {"_id": 1})]
    picked, by = ai_news.relevant(fresh, now) if fresh else ([], "gemini")
    items = picked + from_events(now) + from_transport(now) + load_notes()
    summary = {"fetched": len(fetched), "new_from_feeds": len(fresh), "relevant": len(picked), "by": by, "errors": errors,
               "items": items}
    if dry_run:
        return summary
    stored = record_news(items, now)
    stored["duplicates"] += merge_duplicates()
    summary.update(stored)
    activity.log("news_refreshed", f"Local news: read {len(fetched)} headline(s), {len(picked)} worth knowing "
                 f"({'Gemini' if by == 'gemini' else 'keyword filter, Gemini unavailable'}), {stored['new']} new"
                 + (f", {stored['duplicates']} duplicate(s) merged" if stored["duplicates"] else "")
                 + (f"; {len(errors)} feed(s) failed" if errors else ""),
                 new=stored["new"], duplicates=stored["duplicates"], by=by, errors=errors)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Local news near the pitches (awareness only).")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("query", nargs="?", default="")
    args = parser.parse_args()
    if args.refresh:
        s = refresh(dry_run=args.dry_run)
        print(f"fetched {s['fetched']} ({s['new_from_feeds']} new), relevant {s['relevant']} by {s['by']}, errors {s['errors']}")
        for i in s["items"]:
            print(f"  [{i.get('kind')}] {i.get('headline')} | {i.get('summary', '')[:90]}")
        if not args.dry_run:
            print(f"stored: new {s['new']}, updated {s['updated']}, duplicates {s['duplicates']}, skipped {s['skipped']}")
    feed = search.news_feed(get_db(), args.query)
    print(f"feed ({feed.mode}):")
    for h in feed.hits:
        print(f"  {h.get('score', 0):.3f} [{h['kind']}] {h['headline']}  ({h['source']}, {h.get('distance_km', '—')} km)")


if __name__ == "__main__":
    main()
