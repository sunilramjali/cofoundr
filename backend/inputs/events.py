"""Events near the truck's pitches: Ticketmaster Discovery API → one plain dict per event.

Returns data only: storing it is the middle's job (inputs never write MongoDB directly). Needs
TICKETMASTER_API_KEY in .env (free at developer.ticketmaster.com). Failures raise fetch.FetchError;
cancelled or malformed events are left out, an area with no events is just [].

    python -m backend.inputs.events             # next 7 days within 3 km of every pitch
"""
import argparse
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from dotenv import load_dotenv

from backend.inputs import fetch
from backend.inputs.pitches import PITCHES, Pitch

load_dotenv()

TICKETMASTER = "https://app.ticketmaster.com/discovery/v2/events.json"
RADIUS_KM = 3
PAGE_SIZE = 100
MAX_PAGES = 5  # Ticketmaster stops paging at 1,000 results anyway; 500 events in 3 km is plenty
MAX_DAYS = 31


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _name(part: Optional[dict]) -> Optional[str]:
    name = (part or {}).get("name")
    return None if not name or name == "Undefined" else name


def _parse(raw: dict, pitch: Pitch, fetched_at: str) -> Optional[dict]:
    """One Ticketmaster event → our dict, or None to leave it out (cancelled, no id/name/date)."""
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
        return None
    dates = raw.get("dates") or {}
    start = dates.get("start") or {}
    status = (dates.get("status") or {}).get("code") or "unknown"
    try:
        day = date.fromisoformat(start.get("localDate") or "").isoformat()
    except ValueError:
        return None
    if status == "cancelled":
        return None
    venue = (((raw.get("_embedded") or {}).get("venues")) or [{}])[0] or {}
    lat, lon = _float((venue.get("location") or {}).get("latitude")), _float((venue.get("location") or {}).get("longitude"))
    distance = _km(pitch["lat"], pitch["lon"], lat, lon) if lat is not None and lon is not None else _float(raw.get("distance"))
    classes = raw.get("classifications") or [{}]
    primary = next((c for c in classes if c.get("primary")), classes[0]) or {}
    return {
        "source": "ticketmaster", "source_id": raw["id"], "name": raw["name"],
        "category": _name(primary.get("segment")), "genre": _name(primary.get("genre")),
        "date": day, "starts_at": start.get("dateTime"),  # UTC ISO, or None when the time is TBA
        "status": status, "venue": venue.get("name"), "lat": lat, "lon": lon,
        "distance_km": None if distance is None else round(distance, 2),
        "pitch_id": pitch["id"], "url": raw.get("url"), "fetched_at": fetched_at,
    }


def nearby(pitch: Pitch = PITCHES[0], days: int = 7, radius_km: float = RADIUS_KM,
           now: Optional[datetime] = None, api_key: Optional[str] = None) -> List[dict]:
    """Events in the next `days` days within `radius_km` of a pitch, soonest first."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}.")
    if not 0 < radius_km <= 50:
        raise ValueError("radius_km must be between 0 and 50.")
    key = api_key or os.environ.get("TICKETMASTER_API_KEY")
    if not key:
        raise fetch.FetchError("TICKETMASTER_API_KEY is not set: add it to .env (free at developer.ticketmaster.com)")
    start = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    window = {"startDateTime": f"{start:%Y-%m-%dT%H:%M:%SZ}", "endDateTime": f"{start + timedelta(days=days):%Y-%m-%dT%H:%M:%SZ}"}
    fetched_at = datetime.now(timezone.utc).isoformat()

    found = {}
    for page in range(MAX_PAGES):
        data = fetch.get_json(TICKETMASTER, {
            "apikey": key, "latlong": f"{pitch['lat']},{pitch['lon']}", "radius": radius_km, "unit": "km",
            **window, "size": PAGE_SIZE, "page": page, "sort": "date,asc",
        }, source="Ticketmaster")
        for raw in (data.get("_embedded") or {}).get("events") or []:
            event = _parse(raw, pitch, fetched_at)
            if event:
                found.setdefault(event["source_id"], event)  # an event can repeat across pages
        if page + 1 >= ((data.get("page") or {}).get("totalPages") or 1):
            break
    return sorted(found.values(), key=lambda e: (e["date"], e["starts_at"] or "~"))


def nearby_all(days: int = 7, radius_km: float = RADIUS_KM) -> List[dict]:
    """nearby() for every pitch."""
    return [e for pitch in PITCHES for e in nearby(pitch, days, radius_km)]


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Show Ticketmaster events near GAIA's pitches.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--radius", type=float, default=RADIUS_KM, help="km around each pitch")
    args = parser.parse_args(argv)
    try:
        events = nearby_all(args.days, args.radius)
    except fetch.FetchError as e:
        raise SystemExit(f"⚠️ {e}")
    for e in events:
        print(f"{e['date']}  {e['distance_km']} km  {e['name']} @ {e['venue']} [{e['category'] or '?'}]")
    print(f"{len(events)} event(s).")


if __name__ == "__main__":
    main()
