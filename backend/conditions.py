"""Recording weather + events around the truck's pitches: the hand-off from inputs (Jade) to the database (Sunil).

Input code passes days and events **exactly as backend/inputs/weather.py and events.py return them**
(formats in backend/inputs/README.md and the WeatherDay / Event shapes in schema.py):

    from backend.inputs import weather
    from backend.conditions import record_events, record_weather, windows
    record_weather(weather.forecast_all(days=7))           # ConditionsResult(new, updated, unchanged, errors)
    record_events(any_source_events, covered=windows(7))    # + removed: events gone from the source (any source)

Safe to run any number of times: unchanged days/events are left alone, changed ones are updated (forecasts improve as
the day nears; events get rescheduled). Never raises on bad data: bad records are reported in `errors`, the rest
are still recorded. The forecast reads it all back with conditions_for().

    python -m backend.conditions --sync [--days 7]   # fetch the weather from Open-Meteo and record it
                                                     # (events: no source wired up yet; Ticketmaster parked)
    python -m backend.conditions                     # show what's stored for the coming week
"""
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.db import activity, schema
from backend.db.client import get_db

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")
WEATHER_NUMBERS = ("temp_max_c", "temp_min_c", "precip_mm", "precip_chance_pct", "wind_max_kmh")
EVENT_FIELDS = ("name", "category", "genre", "date", "starts_at", "status", "venue", "lat", "lon", "distance_km",
                "pitch_id", "url")
REMOVED = "removed"


@dataclass
class ConditionsResult:
    kind: str  # "weather" | "events"
    new: int = 0
    updated: int = 0  # stored before, something changed (better forecast, rescheduled event)
    unchanged: int = 0
    removed: int = 0  # events no longer listed by the source for a window we re-fetched (cancelled or moved)
    errors: List[str] = field(default_factory=list)  # "<record>: what's wrong"; those records were NOT stored

    @property
    def message(self) -> str:
        unit = "day(s)" if self.kind == "weather" else "event(s)"
        parts = [f"{self.new} new", f"{self.updated} updated", f"{self.unchanged} unchanged"]
        if self.removed:
            parts.append(f"{self.removed} no longer listed")
        if self.errors:
            parts.append(f"{len(self.errors)} rejected")
        return f"{self.kind.capitalize()} sync ({unit}): " + ", ".join(parts) + "."


def windows(days: int = 7, today: Optional[date] = None) -> List[Tuple[str, str, str]]:
    """(pitch_id, first day, last day) for every pitch: what a nearby_all(days) fetch covered."""
    from backend.inputs.pitches import PITCHES

    first = today or datetime.now(LONDON).date()
    return [(p["id"], first.isoformat(), (first + timedelta(days=days - 1)).isoformat()) for p in PITCHES]


def record_weather(days: Iterable[dict], now: Optional[datetime] = None) -> ConditionsResult:
    """Store the latest forecast per pitch per day. Idempotent; a changed forecast replaces the old one."""
    db, now = get_db(), now or datetime.now(timezone.utc)
    result, batch = ConditionsResult("weather"), {}
    for raw in days:
        try:
            doc = _weather_doc(raw, now)
        except ValueError as e:
            result.errors.append(f"{_label(raw, 'pitch_id', 'date')}: {e}")
            continue
        batch[doc["_id"]] = doc  # the same day twice in one batch: the last one wins
    for doc in batch.values():
        _upsert(db[schema.WEATHER], doc, ("summary", "weather_code") + WEATHER_NUMBERS, result, now)
    if result.new or result.updated or result.errors:
        nxt = min(batch.values(), key=lambda d: d["date"], default=None)
        extra = f" Next: {_weather_line(nxt)}." if nxt else ""
        activity.log("weather_synced", result.message + extra, new=result.new, updated=result.updated, errors=result.errors[:5])
    return result


def record_events(events: Iterable[dict], covered: Optional[List[Tuple[str, str, str]]] = None,
                  now: Optional[datetime] = None) -> ConditionsResult:
    """Store events near the pitches. Idempotent. `covered` = the (pitch_id, first day, last day) windows this fetch
    looked at: stored events in those windows that weren't returned again are marked "removed", never deleted."""
    db, now = get_db(), now or datetime.now(timezone.utc)
    result, batch = ConditionsResult("events"), {}
    for raw in events:
        try:
            doc = _event_doc(raw, now)
        except ValueError as e:
            result.errors.append(f"{_label(raw, 'source_id', 'name')}: {e}")
            continue
        seen = batch.get(doc["_id"])  # found near two pitches: keep the nearest
        if not seen or (doc["distance_km"] or 1e9) < (seen["distance_km"] or 1e9):
            batch[doc["_id"]] = doc
    coll = db[schema.EVENTS]
    for doc in batch.values():
        first_seen = (coll.find_one({"_id": doc["_id"]}, {"first_seen_at": 1}) or {}).get("first_seen_at", now)
        _upsert(coll, {**doc, "first_seen_at": first_seen}, EVENT_FIELDS, result, now)
    for pitch_id, first, last in covered or []:
        gone = coll.update_many({"business_id": BUSINESS_ID, "pitch_id": pitch_id, "date": {"$gte": first, "$lte": last},
                                 "status": {"$ne": REMOVED}, "_id": {"$nin": list(batch)}},
                                {"$set": {"status": REMOVED, "updated_at": now}})
        result.removed += gone.modified_count
    if result.new or result.updated or result.removed or result.errors:
        activity.log("events_synced", result.message, new=result.new, updated=result.updated, removed=result.removed,
                     errors=result.errors[:5])
    return result


def conditions_for(dates: List[str], pitch_id: Optional[str] = None) -> Dict[str, dict]:
    """{date: {"weather": WeatherDay or None, "events": [Event, nearest first]}} for the forecast. Removed events
    are left out. pitch_id=None → the first pitch's weather and events near any pitch."""
    from backend.inputs.pitches import PITCHES

    db = get_db()
    pitch = pitch_id or PITCHES[0]["id"]
    out = {d: {"weather": None, "events": []} for d in dates}
    for w in db[schema.WEATHER].find({"business_id": BUSINESS_ID, "pitch_id": pitch, "date": {"$in": dates}}):
        out[w["date"]]["weather"] = w
    query = {"business_id": BUSINESS_ID, "date": {"$in": dates}, "status": {"$ne": REMOVED}}
    if pitch_id:
        query["pitch_id"] = pitch_id
    for e in db[schema.EVENTS].find(query).sort("distance_km", 1):
        out[e["date"]]["events"].append(e)
    return out


# ---------------------------------------------------------------- helpers

def _upsert(coll, doc: dict, compare: Tuple[str, ...], result: ConditionsResult, now: datetime) -> None:
    old = coll.find_one({"_id": doc["_id"]})
    if old is None:
        coll.insert_one({**doc, "updated_at": now})
        result.new += 1
    elif any(_same(old.get(k)) != _same(doc.get(k)) for k in compare):
        coll.replace_one({"_id": doc["_id"]}, {**doc, "updated_at": now})
        result.updated += 1
    else:  # nothing changed: just remember we saw it again
        coll.update_one({"_id": doc["_id"]}, {"$set": {"fetched_at": doc["fetched_at"],
                                                       **({"last_seen_at": now} if "last_seen_at" in doc else {})}})
        result.unchanged += 1


def _same(value):
    """Compare stored and new values fairly: Mongo hands back datetimes without microsecond precision issues, floats as floats."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0)
    if isinstance(value, float):
        return round(value, 4)
    return value


def _weather_doc(raw: dict, now: datetime) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("not a weather day")
    pitch, day = _text(raw.get("pitch_id"), "pitch_id"), _day(raw.get("date"))
    code = raw.get("weather_code")
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise ValueError(f"weather_code is not a whole number: {code!r}")
    return {
        "_id": f"{BUSINESS_ID}:{pitch}:{day}", "business_id": BUSINESS_ID, "pitch_id": pitch, "date": day,
        "summary": raw.get("summary") or "No data", "weather_code": code,
        **{k: _number(raw.get(k), k) for k in WEATHER_NUMBERS},
        "source": raw.get("source") or "open-meteo", "fetched_at": _timestamp(raw.get("fetched_at"), now), "synthetic": False,
    }


def _event_doc(raw: dict, now: datetime) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("not an event")
    source, source_id = _text(raw.get("source"), "source"), _text(raw.get("source_id"), "source_id")
    name = _text(raw.get("name"), "name")
    starts = raw.get("starts_at")
    try:
        starts_at = _timestamp(starts, None) if starts else None
    except ValueError:
        starts_at = None  # a garbled start time is "time TBA", not a reason to lose the event
    category, genre, venue = raw.get("category"), raw.get("genre"), raw.get("venue")
    return {
        "_id": f"{BUSINESS_ID}:{source}:{source_id}", "business_id": BUSINESS_ID, "source": source, "source_id": source_id,
        "name": name, "category": category, "genre": genre, "date": _day(raw.get("date")), "starts_at": starts_at,
        "status": raw.get("status") or "unknown", "venue": venue,
        "lat": _number(raw.get("lat"), "lat"), "lon": _number(raw.get("lon"), "lon"),
        "distance_km": _number(raw.get("distance_km"), "distance_km"), "pitch_id": _text(raw.get("pitch_id"), "pitch_id"),
        "url": raw.get("url"),
        "search_text": " | ".join([name, ", ".join(p for p in (category, genre) if p), venue or ""]),
        "fetched_at": _timestamp(raw.get("fetched_at"), now), "last_seen_at": now, "synthetic": False,
    }


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {name}")
    return value.strip()


def _day(value) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError(f"date is not YYYY-MM-DD: {value!r}") from None


def _number(value, name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is not a number: {value!r}")
    return float(value)


def _timestamp(value, default: Optional[datetime]) -> datetime:
    """ISO string or datetime → aware UTC. Missing → default (now). No offset → London time."""
    if value is None and default is not None:
        return default
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(dt, datetime):
        raise ValueError(f"not a timestamp: {value!r}")
    return (dt if dt.tzinfo else dt.replace(tzinfo=LONDON)).astimezone(timezone.utc)


def _label(raw, *keys) -> str:
    return "/".join(str(raw.get(k)) for k in keys) if isinstance(raw, dict) else repr(raw)[:40]


def _weather_line(w: dict) -> str:
    day = date.fromisoformat(w["date"])
    temps = f" {w['temp_min_c']:.0f}–{w['temp_max_c']:.0f}°C" if w["temp_min_c"] is not None and w["temp_max_c"] is not None else ""
    rain = f", rain {w['precip_chance_pct']:.0f}%" if w["precip_chance_pct"] is not None else ""
    return f"{day:%a %d %b} {w['summary']}{temps}{rain}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Record weather + events around the pitches.")
    parser.add_argument("--sync", action="store_true", help="fetch the weather from Open-Meteo and record it")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    if args.sync:
        from backend.inputs import weather
        from backend.inputs.fetch import FetchError

        try:
            print(record_weather(weather.forecast_all(days=args.days)).message)
        except FetchError as e:  # stored forecasts are kept as they are
            activity.log("weather_sync_failed", f"Weather sync failed: {e}")
            print(f"weather: {e}")
    today = datetime.now(LONDON).date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(args.days)]
    for d, c in conditions_for(dates).items():
        w = _weather_line(c["weather"]) if c["weather"] else f"{d} no weather stored"
        print(f"{w}" + "".join(f"\n    · {e['name']} at {e['venue']} ({e['distance_km']} km)" for e in c["events"]))


if __name__ == "__main__":
    main()
