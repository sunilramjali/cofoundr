"""Read-only helpers that turn MongoDB documents into what the dashboard shows. No writes, never raises on bad data."""
import threading
import time
from datetime import date, datetime, timezone
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
