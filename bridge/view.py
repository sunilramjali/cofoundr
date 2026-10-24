"""Read-only helpers that turn MongoDB documents into what the dashboard shows. No writes, never raises on bad data."""
import math
import re
import threading
import time
from datetime import date, datetime, time as dtime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.inputs import weather
from backend.inputs.pitches import PITCHES

LONDON = ZoneInfo("Europe/London")
WEATHER_TTL = 30 * 60  # seconds; Open-Meteo updates hourly at best
WEATHER_RETRY = 5 * 60  # after a failure, don't slow every page load by retrying

_weather = {"at": 0.0, "ttl": 0, "days": {}}
_weather_lock = threading.Lock()

# activity kind → (icon, short label); unknown kinds fall back to a dot
ACTIVITY = {
    "invoice_extracted": ("doc", "Read an invoice"),
    "ingest_failed": ("alert", "Couldn't read a document"),
    "gmail_checked": ("mail", "Checked the inbox"),
    "gmail_check_failed": ("alert", "Inbox check failed"),
    "sales_synced": ("till", "Synced the till"),
    "forecast_updated": ("chart", "Planned the week"),
    "overdue_check": ("clock", "Checked for overdue invoices"),
    "approval_decided": ("check", "Decision"),
    "approval_cancelled": ("cross", "Withdrew an action"),
    "send_failed": ("alert", "Couldn't send"),
    "brief_served": ("chat", "Sent the brief"),
}
TONES = {0: "Friendly nudge", 1: "Firm reminder", 2: "Final notice"}


def london_now() -> datetime:
    return datetime.now(LONDON)


def days_ago(now: datetime, days: int) -> datetime:
    """London midnight `days` days before `now`'s date (for "since" queries)."""
    return datetime.combine(date.fromordinal(now.date().toordinal() - days), dtime.min, tzinfo=LONDON)


def greeting(now: datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"


def ago(when: Optional[datetime], now: Optional[datetime] = None) -> str:
    """'just now', '4 min ago', '3 h ago', 'yesterday', '12 Oct'."""
    if not when:
        return "never"
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    secs = ((now or datetime.now(timezone.utc)) - when).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    if secs < 172800:
        return "yesterday"
    return when.astimezone(LONDON).strftime("%d %b")


def money0(value) -> str:
    return "–" if value is None else f"£{value:,.0f}"


def weather_icon(code: Optional[int]) -> str:
    """WMO code → one of our inline icons."""
    if code is None:
        return "none"
    if code <= 1:
        return "sun"
    if code == 2:
        return "partsun"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    return "storm"


def weather_by_date() -> Dict[str, dict]:
    """The first pitch's 7-day forecast keyed by date, cached; {} when Open-Meteo can't be reached."""
    with _weather_lock:
        if time.monotonic() - _weather["at"] < _weather["ttl"]:
            return _weather["days"]
        try:
            days, ttl = {d["date"]: d for d in weather.forecast(PITCHES[0], days=7)}, WEATHER_TTL
        except Exception:  # the page must render without weather
            days, ttl = {}, WEATHER_RETRY
        _weather.update(at=time.monotonic(), ttl=ttl, days=days)
        return days


def week(forecasts: List[dict], weather_days: Dict[str, dict], today: date) -> dict:
    """The forecast strip: one entry per day with bar heights as % of the week's highest expected revenue."""
    peak = max([f.get("high_revenue") or f.get("final_revenue") or 0 for f in forecasts] + [1])
    days = []
    for f in forecasts:
        try:
            day = date.fromisoformat(f["date"])
        except (KeyError, TypeError, ValueError):
            continue
        w = weather_days.get(f["date"]) or {}
        revenue = f.get("final_revenue") or f.get("expected_revenue") or 0
        days.append({
            "date": day, "is_today": day == today, "trading": bool(f.get("trading")),
            "revenue": revenue, "low": f.get("low_revenue") or 0, "high": f.get("high_revenue") or 0,
            "bar": round(100 * revenue / peak), "low_pct": round(100 * (f.get("low_revenue") or 0) / peak),
            "high_pct": round(100 * (f.get("high_revenue") or 0) / peak),
            "orders": f.get("expected_orders") or 0, "basis": f.get("basis") or "",
            "products": (f.get("products") or [])[:3], "adjustment": f.get("adjustment"),
            "wx": w, "wx_icon": weather_icon(w.get("weather_code")) if w else "none",
        })
    trading = [d for d in days if d["trading"]]
    return {"days": days, "total": sum(d["revenue"] for d in trading), "trading_days": len(trading),
            "next": next((d for d in trading if d["date"] >= today), None)}


def timeline(entries: List[dict], now: datetime) -> List[dict]:
    out = []
    for e in entries:
        icon, label = ACTIVITY.get(e.get("kind"), ("dot", (e.get("kind") or "update").replace("_", " ").capitalize()))
        out.append({"icon": icon, "label": label, "summary": e.get("summary") or "", "ago": ago(e.get("at"), now),
                    "at": e.get("at"), "actor": e.get("actor") or "agent", "kind": e.get("kind")})
    return out


def headline(pending: List[dict], owed_overdue: float, review: List[dict]) -> str:
    """The hero sentence: what needs the owner, in plain words."""
    n = len(pending)
    if not n and not review:
        lead = "Nothing needs you right now."
    else:
        bits = []
        if n:
            bits.append(f"{'One thing needs' if n == 1 else f'{_words(n)} things need'} your yes")
        if review:
            bits.append(f"{_words(len(review)).lower()} invoice{'s' if len(review) > 1 else ''} need{'s' if len(review) == 1 else ''} a second look")
        lead = (", and ".join(bits) + ".")
        lead = lead[0].upper() + lead[1:]
    if owed_overdue:
        lead += f" {money0(owed_overdue)} is overdue to come in."
    return lead


def _words(n: int) -> str:
    return ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"][n] if n <= 10 else str(n)


def due_label(invoice: dict, today: date) -> str:
    """'Paid', '21 days overdue', 'Due today', 'Due in 3 days', 'Due 17 Nov'."""
    if invoice.get("status") == "paid":
        return "Paid"
    due = invoice.get("due_date")
    if not isinstance(due, datetime):
        return "No due date"
    days = (due.astimezone(LONDON).date() - today).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "Due today"
    if days <= 7:
        return f"Due in {days} day{'s' if days != 1 else ''}"
    return f"Due {due.astimezone(LONDON):%d %b}"


def sales_history(orders: List[dict], today: date, days: int = 28) -> dict:
    """Till takings per London day for the last `days` days (today excluded: it's still trading)."""
    by_day: Dict[date, dict] = {}
    for o in orders:
        at = o.get("placed_at")
        if not isinstance(at, datetime):
            continue
        day = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(LONDON).date()
        if 0 < (today - day).days <= days:
            d = by_day.setdefault(day, {"revenue": 0.0, "orders": 0})
            d["revenue"] += o.get("total") or 0
            d["orders"] += 1
    peak = max([d["revenue"] for d in by_day.values()] + [1])
    out = []
    for i in range(days, 0, -1):
        day = date.fromordinal(today.toordinal() - i)
        d = by_day.get(day, {"revenue": 0.0, "orders": 0})
        out.append({"date": day, "revenue": round(d["revenue"], 2), "orders": d["orders"], "bar": round(100 * d["revenue"] / peak)})
    trading = [d for d in out if d["orders"]]
    return {"days": out, "total": round(sum(d["revenue"] for d in trading), 2), "trading_days": len(trading),
            "last": trading[-1] if trading else None,
            "average": round(sum(d["revenue"] for d in trading) / len(trading), 2) if trading else 0}


LOW_COVER_DAYS = 7  # flag stock that will last less than a week
ORDER_COVER_DAYS = 14  # suggested order tops stock up to two weeks
OPPORTUNITY = re.compile(r"partner|collab|opportunit|pop-?up|sponsor|event", re.IGNORECASE)


def stock_estimate(counts: List[dict], now: datetime) -> dict:
    """What's probably on the truck today: the latest physical count minus usage since, where usage per day comes from
    the most recent opening → closing pair of counts. An estimate (deliveries aren't recorded), labelled as such."""
    counts = sorted([c for c in counts if isinstance(c.get("counted_at"), datetime)], key=lambda c: c["counted_at"])
    if not counts:
        return {"rows": [], "low": [], "counted_at": None, "days_since": None, "basis": None}
    latest = counts[-1]
    usage, basis = {}, None
    for close in reversed(counts):
        opening = next((c for c in reversed(counts) if c.get("kind") == "opening" and c["counted_at"] < close["counted_at"]), None)
        if close.get("kind") == "closing" and opening:
            days = max(1, (close["counted_at"] - opening["counted_at"]).days)
            before = {i["sku"]: i.get("qty") or 0 for i in opening.get("items", [])}
            usage = {i["sku"]: max(0.0, (before.get(i["sku"], 0) - (i.get("qty") or 0)) / days) for i in close.get("items", [])}
            basis = f"usage between the {opening['counted_at'].astimezone(LONDON):%d %b} and {close['counted_at'].astimezone(LONDON):%d %b} counts"
            break
    since = max(0, (now - latest["counted_at"]).days)
    items = []
    for i in latest.get("items", []):
        per_day = usage.get(i["sku"])
        est = (i.get("qty") or 0) - (per_day or 0) * since
        cover = est / per_day if per_day else None
        items.append({"sku": i["sku"], "name": i.get("item_name") or i["sku"], "unit": i.get("unit") or "", "counted": i.get("qty") or 0,
                      "per_day": per_day, "estimate": max(0.0, est), "out": est <= 0, "cover": None if cover is None else max(0.0, cover),
                      "low": cover is not None and cover < LOW_COVER_DAYS,
                      "order": max(0, math.ceil(per_day * ORDER_COVER_DAYS - max(0.0, est))) if per_day else 0})
    items.sort(key=lambda x: (x["cover"] is None, x["cover"] if x["cover"] is not None else 0))
    return {"rows": items, "low": [x for x in items if x["low"]], "counted_at": latest["counted_at"], "days_since": since, "basis": basis}


def opportunities(gmail_logs: List[dict]) -> List[dict]:
    """Partnership / event emails the inbox check set aside (newest first, one per subject)."""
    seen, out = set(), []
    for log in gmail_logs:
        for e in (log.get("detail") or {}).get("emails") or []:
            subject = (e.get("subject") or "").strip()
            if subject and OPPORTUNITY.search(subject) and subject.lower() not in seen:
                seen.add(subject.lower())
                who = re.search(r"[–-]\s*(.+?)\s+[x×]\s+", subject)
                out.append({"subject": subject, "sender": e.get("from") or "", "at": log.get("at"),
                            "who": who.group(1).strip() if who else subject})
    return out


def trend(sales: dict, wk: dict, days: int = 14) -> dict:
    """SVG geometry for 'takings vs forecast': the last `days` trading days of actuals, then the forecast days ahead."""
    actual = [d for d in sales.get("days", []) if d["orders"]][-days:]
    ahead = [d for d in wk.get("days", []) if d["trading"]]
    values = [d["revenue"] for d in actual] + [d["revenue"] for d in ahead]
    if len(values) < 2:
        return {"actual": "", "forecast": "", "area": "", "dot": None, "labels": []}
    top, low = max(values) * 1.08, min(values) * .82
    n = len(values)
    pt = lambda i, v: (round(100 * i / (n - 1), 2), round(100 - 100 * (v - low) / (top - low or 1), 2))  # noqa: E731
    pts = [pt(i, v) for i, v in enumerate(values)]
    split = len(actual) - 1
    line = lambda ps: " ".join(f"{'M' if k == 0 else 'L'}{x},{y}" for k, (x, y) in enumerate(ps))  # noqa: E731
    return {"actual": line(pts[:split + 1]), "forecast": line(pts[split:]) if ahead else "",
            "area": line(pts[:split + 1]) + f" L{pts[split][0]},100 L0,100 Z", "dot": pts[split] if actual else None,
            "split": pts[split][0] if actual else 0}
