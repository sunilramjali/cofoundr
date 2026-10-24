"""Which TfL disruptions actually matter to a pitch (MVP2): the middle's reading of backend/inputs/transport.py.

TfL reports problems per LINE, but a closure "between Sydenham and Crystal Palace" on the Windrush line does nothing
to Haggerston, 15 stops away. For each status near a pitch this decides:
- which nearby stations it hits: a station named in the text; or on the path between the two stations named in
  "no service between A and B" (path found on the line's own station map); or the whole line (suspended, delays)
- impact: "closed" (no trains at that station), "delays", or "uncertain" when the text can't be pinned down
  (stored and shown as uncertain, never guessed either way)
- which London dates it touches during trading hours (a closure after 23:30 doesn't cost the truck a coffee)
Disruptions that hit none of our stations, or only outside trading hours, are returned as `ignored` with the reason.

    assess(snapshot) -> (disruptions, ignored)       # snapshot = backend.inputs.transport.snapshot()
"""
import hashlib
import re
from collections import deque
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
TRADING = (time(7, 0), time(18, 0))  # the truck's day: disruptions outside it don't matter
WHOLE_LINE_CLOSED = {"Closed", "Planned Closure", "Suspended", "Not Running"}
SECTION_CLOSED = {"Part Closure", "Part Suspended"}
DELAYS = {"Severe Delays", "Minor Delays", "Reduced Service", "Issues Reported", "Change of frequency", "Diverted",
          "Bus Service", "Part Closed"}
# Everything else ("Special Service", "Service Closed" = normal end of service at night, ...) isn't a disruption to us
BETWEEN = re.compile(r"\bbetween (.+?)(?=[,.;:/()]|$|\s(?:due|while|until|after|before|from|for|because|each|every|on)\b)",
                     re.IGNORECASE)


def assess(snapshot: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """Per pitch: the disruptions that hit a nearby station during trading hours, and what was ignored (and why)."""
    found, ignored = [], []
    for pitch_id, snap in snapshot.items():
        first, last = (date.fromisoformat(d) for d in snap["window"])
        for status in snap["statuses"]:
            near = [s for s in snap["stations"] if status["line_id"] in s["lines"]]
            doc, why = _assess_one(pitch_id, status, near, snap.get("sequences", {}).get(status["line_id"], []), first, last)
            if doc:
                found.append(doc)
            else:
                ignored.append({"pitch_id": pitch_id, "line": status["line_name"], "status": status["status"],
                                "reason": status["reason"][:160], "why": why})
    return found, ignored


def _assess_one(pitch_id: str, status: dict, near: List[dict], branches: List[List[str]], first: date, last: date):
    kind = status["status"]
    if kind in WHOLE_LINE_CLOSED or kind in SECTION_CLOSED:
        impact = "closed"
    elif kind in DELAYS:
        impact = "delays"
    else:
        return None, f"'{kind}' isn't a disruption for passengers"
    if not near:
        return None, "none of our stations are on this line"

    text = _norm(status["reason"])
    named = [s for s in near if re.search(rf"\b{re.escape(_norm(s['name']))}\b", text)]
    pairs = [m.group(1) for m in BETWEEN.finditer(status["reason"])]
    if named:
        hit = named
    elif pairs:
        sections = [p for p in (_section(span, branches) for span in pairs) if p is not None]
        if sections:
            on_path = set().union(*sections)
            hit = [s for s in near if _norm(s["name"]) in on_path]
            if not hit:
                return None, "the affected section doesn't include our stations"
        else:
            hit, impact = near, "uncertain"  # TfL named a section we couldn't place on the line's map
    elif kind in SECTION_CLOSED:
        hit, impact = near, "uncertain"  # "part closure" with no section given
    else:
        hit = near  # the whole line: suspended, or delays

    days = _trading_days(status.get("periods") or [], first, last)
    if not days:
        return None, "only outside trading hours (07:00–18:00) or outside this week"
    starts = min((p["from"] for p in status.get("periods") or []), default=None)
    ends = max((p["to"] for p in status.get("periods") or []), default=None)
    key = "|".join([status["line_id"], kind, status["reason"]])
    return {
        "source": status.get("source", "tfl"), "source_id": hashlib.sha1(key.encode()).hexdigest()[:16],
        "pitch_id": pitch_id, "line_id": status["line_id"], "line_name": status["line_name"], "status": kind,
        "impact": impact, "category": "planned" if status.get("category") == "PlannedWork" else "live",
        "reason": status["reason"], "dates": days, "stations": [_short(s["name"]) for s in hit],
        "nearest_m": min(s["distance_m"] for s in hit), "starts_at": starts, "ends_at": ends,
        "fetched_at": status.get("fetched_at"),
    }, None


def _section(span: str, branches: List[List[str]]) -> Optional[set]:
    """Stations on the path between the two ends of "A and B" (normalised names), or None if it can't be placed.
    Station names can contain "and" ("Elephant and Castle"), so every split is tried."""
    graph = _graph(branches)
    words = span.split(" and ")
    for i in range(1, len(words)):
        a, b = _node(" and ".join(words[:i]), graph), _node(" and ".join(words[i:]), graph)
        if a and b:
            path = _path(graph, a, b)
            if path:
                return set(path)
    return None


def _graph(branches: List[List[str]]) -> Dict[str, set]:
    """TfL's sequences are pieces of the line that share junction stations: join them into one map."""
    graph: Dict[str, set] = {}
    for branch in branches:
        names = [_norm(n) for n in branch]
        for a, b in zip(names, names[1:]):
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
    return graph


def _node(name: str, graph: Dict[str, set]) -> Optional[str]:
    n = _norm(name)
    if not n:
        return None
    if n in graph:
        return n
    starts = [g for g in graph if g.startswith(n + " ") or n.startswith(g + " ")]
    return starts[0] if len(starts) == 1 else None  # ambiguous ("New Cross" vs "New Cross Gate") → can't place


def _path(graph: Dict[str, set], a: str, b: str) -> Optional[List[str]]:
    previous, queue = {a: None}, deque([a])
    while queue:
        here = queue.popleft()
        if here == b:
            path = []
            while here is not None:
                path.append(here)
                here = previous[here]
            return path
        for nxt in graph.get(here, ()):
            if nxt not in previous:
                previous[nxt] = here
                queue.append(nxt)
    return None


def _trading_days(periods: List[dict], first: date, last: date) -> List[str]:
    """London dates in [first, last] where a period overlaps trading hours. No periods = today only (live status)."""
    if not periods:
        today = datetime.now(LONDON).date()
        return [today.isoformat()] if first <= today <= last else []
    days = set()
    for p in periods:
        try:
            start, end = _london(p["from"]), _london(p["to"])
        except (KeyError, TypeError, ValueError):
            continue
        day = max(start.date(), first)
        while day <= min(end.date(), last):
            open_, close = (datetime.combine(day, t, LONDON) for t in TRADING)
            if start < close and end > open_:
                days.add(day.isoformat())
            day += timedelta(days=1)
    return sorted(days)


def _london(value: str) -> datetime:
    """TfL sends times without an offset; they're UTC ("2026-10-25T04:30:00Z" or "2026-10-25T04:30:00")."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(LONDON)


def _norm(name: str) -> str:
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"\((?:london)\)|\b(?:rail|underground|dlr|overground|elizabeth line)? ?station\b", " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def _short(name: str) -> str:
    return re.sub(r"\s*\(London\)|\s+(?:Rail|Underground|DLR) Station$", "", name).strip()
