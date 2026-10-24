"""Transport around the truck's pitches: TfL's Unified API (free; optional TFL_APP_KEY) → plain dicts.

Three reads, all data only (deciding which disruptions matter to a pitch is backend/transport.py, storing them is
backend/conditions.py; inputs never write MongoDB):
- stations(pitch): tube/Overground/Elizabeth line/rail/DLR stations within walking distance, with their lines
- statuses(line_ids, start, end): every non-"Good Service" status on those lines in a date range: live delays
  and planned works (weekend closures), with TfL's own text and validity periods
- sequences(line_id): the line's stations in order, per branch, to tell whether "no service between A and B"
  includes our station (TfL rarely fills in its affected-stops list)

Failures raise fetch.FetchError with a plain-English message (the app key never appears in it).

    python -m backend.inputs.transport          # stations near each pitch + disruptions this week
"""
import argparse
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.inputs import fetch
from backend.inputs.pitches import PITCHES, Pitch

TFL = "https://api.tfl.gov.uk"
LONDON = ZoneInfo("Europe/London")
RADIUS_M = 1200  # ~15 minutes' walk: stations whose passengers might pass the truck
MODES = ["tube", "overground", "elizabeth-line", "national-rail", "dlr"]
STOP_TYPES = ["NaptanMetroStation", "NaptanRailStation"]
FINE = {"Good Service", "No Issues", "Information"}


def _params(**extra) -> dict:
    key = os.environ.get("TFL_APP_KEY")
    return {**extra, **({"app_key": key} if key else {})}


def stations(pitch: Pitch = PITCHES[0], radius_m: int = RADIUS_M, timeout: float = fetch.TIMEOUT) -> List[dict]:
    """Stations near one pitch, nearest first: {"station_id", "name", "distance_m", "lines", "modes", "pitch_id"}."""
    data = fetch.get_json(f"{TFL}/StopPoint", _params(lat=pitch["lat"], lon=pitch["lon"], radius=radius_m,
                                                      stopTypes=",".join(STOP_TYPES), modes=",".join(MODES)),
                          source="TfL", timeout=timeout)
    points = data.get("stopPoints")
    if not isinstance(points, list):
        raise fetch.FetchError("TfL answered without a list of stations")
    out = []
    for p in points:
        if not isinstance(p, dict) or not p.get("id") or not p.get("commonName"):
            continue  # a malformed entry costs one station, not the whole list
        lines = sorted({ln["id"] for ln in p.get("lines") or [] if isinstance(ln, dict) and ln.get("id")})
        modes = sorted({m for m in p.get("modes") or [] if m in MODES})
        if not lines:
            continue
        out.append({"station_id": p["id"], "name": p["commonName"], "distance_m": round(float(p.get("distance") or 0)),
                    "lines": lines, "modes": modes, "pitch_id": pitch["id"]})
    return sorted(out, key=lambda s: s["distance_m"])


def statuses(line_ids: List[str], start: date, end: date, timeout: float = fetch.TIMEOUT) -> List[dict]:
    """Every status other than good service on these lines between start and end (London dates, inclusive)."""
    if not line_ids:
        return []
    data = fetch.get_json(f"{TFL}/Line/{','.join(sorted(set(line_ids)))}/Status/{start.isoformat()}/to/{end.isoformat()}",
                          _params(), source="TfL", timeout=timeout, expect=list)
    fetched_at = datetime.now(timezone.utc).isoformat()
    out = []
    for line in data:
        if not isinstance(line, dict) or not line.get("id"):
            continue
        for s in line.get("lineStatuses") or []:
            if not isinstance(s, dict) or s.get("statusSeverityDescription") in FINE:
                continue
            disruption = s.get("disruption") or {}
            periods = [{"from": p.get("fromDate"), "to": p.get("toDate")}
                       for p in s.get("validityPeriods") or [] if isinstance(p, dict) and p.get("fromDate") and p.get("toDate")]
            out.append({
                "line_id": line["id"], "line_name": line.get("name") or line["id"],
                "status": s.get("statusSeverityDescription") or "Unknown", "severity": s.get("statusSeverity"),
                "category": disruption.get("category") or "Undefined",  # "PlannedWork" | "RealTime" | ...
                "reason": (s.get("reason") or disruption.get("description") or "").strip(),
                "periods": periods, "source": "tfl", "fetched_at": fetched_at,
            })
    return out


def sequences(line_id: str, timeout: float = fetch.TIMEOUT) -> List[List[str]]:
    """The line's station names in order, one list per branch (TfL's outbound sequences)."""
    data = fetch.get_json(f"{TFL}/Line/{line_id}/Route/Sequence/outbound", _params(), source="TfL", timeout=timeout)
    branches = []
    for seq in data.get("stopPointSequences") or []:
        names = [p.get("name") for p in (seq or {}).get("stopPoint") or [] if isinstance(p, dict) and p.get("name")]
        if names:
            branches.append(names)
    return branches


def snapshot(days: int = 7, today: Optional[date] = None) -> Dict[str, dict]:
    """Everything backend/transport.py needs, per pitch: {"stations", "statuses", "sequences" (lines with a sectional
    closure only), "window": (first day, last day)}. Sequences are fetched only when a status names a section."""
    first = today or datetime.now(LONDON).date()
    last = first + timedelta(days=days - 1)
    out, seq_cache = {}, {}
    for pitch in PITCHES:
        near = stations(pitch)
        found = statuses(sorted({ln for s in near for ln in s["lines"]}), first, last)
        for line_id in {s["line_id"] for s in found if " between " in s["reason"]}:
            if line_id not in seq_cache:
                seq_cache[line_id] = sequences(line_id)
        out[pitch["id"]] = {"stations": near, "statuses": found, "window": (first.isoformat(), last.isoformat()),
                            "sequences": {ln: seq_cache[ln] for ln in seq_cache if any(s["line_id"] == ln for s in found)}}
    return out


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Show stations and TfL disruptions near GAIA's pitches.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        snap = snapshot(args.days)
    except fetch.FetchError as e:
        raise SystemExit(f"⚠️ {e}")
    for pitch_id, s in snap.items():
        print(f"{pitch_id}:")
        for st in s["stations"]:
            print(f"  {st['distance_m']:>5} m  {st['name']:<40} {', '.join(st['lines'])}")
        for d in s["statuses"]:
            print(f"  ! {d['line_name']}: {d['status']} ({d['category']}) {d['reason'][:110]}")


if __name__ == "__main__":
    main()
