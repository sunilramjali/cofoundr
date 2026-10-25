"""Read-only helpers that turn MongoDB documents into what the dashboard shows. No writes, never raises on bad data."""
import math
import re
import threading
import time
from datetime import date, datetime, time as dtime, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from backend.inputs import weather
from backend.inputs.pitches import PITCHES

LONDON = ZoneInfo("Europe/London")
WEATHER_TTL = 30 * 60  # seconds; Open-Meteo updates hourly at best
WEATHER_RETRY = 5 * 60  # after a failure, don't slow every page load by retrying
WEATHER_TIMEOUT = 3  # seconds: the one inline fetch (nothing cached yet) never holds a page longer than this

_weather = {"at": 0.0, "ttl": 0, "days": {}, "refreshing": False}
_weather_lock = threading.Lock()

# activity kind → (icon, short label); unknown kinds fall back to a dot
ACTIVITY = {
    "whatsapp_pushed": ("chat", "Messaged you on WhatsApp"),
    "whatsapp_received": ("chat", "WhatsApp from you"),
    "whatsapp_send_failed": ("alert", "Couldn't send a WhatsApp"),
    "morning_brief_sent": ("chat", "Sent your morning brief"),
    "assistant_answered": ("chat", "Answered your question"),
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
FIXED = re.compile(r"^'(?P<says>.+)': you said (?P<said>[\d.,-]+), the database says (?P<actual>[\d.,-]+)$")


def board_fixes(first_problems: List[str]) -> List[dict]:
    """The board memo's 'fixed on the recheck' lines as {says, said, actual}, for the struck-through chips.
    Lines that aren't a number mismatch (e.g. "can't be checked") are left out."""
    out = []
    for p in first_problems or []:
        m = FIXED.match(p or "")
        if m:
            fix = m.groupdict()
            try:
                actual = float(fix["actual"].replace(",", ""))
                fix["shown"] = f"£{actual:,.2f}" if "£" in fix["says"] else f"{actual:,g}"
            except ValueError:
                fix["shown"] = fix["actual"]
            out.append(fix)
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def while_away(extracted: List[dict], review: List[dict], fee_audit: Optional[dict], memo: Optional[dict],
               record: Optional[dict]) -> List[dict]:
    """Home's "While you were away" list: what the agent did and caught on its own, one line each, newest proof
    first. Built from stored results (never log text), so every number here is one the pages behind it show."""
    out = []
    if extracted or review:
        text = f"Read {_plural(len(extracted), 'invoice')} this week and checked every total" if extracted else ""
        if review:
            flagged = f"flagged {_plural(len(review), 'invoice')} that {'doesn' if len(review) == 1 else 'don'}'t add up"
            text = f"{text}, and {flagged}" if text else flagged[0].upper() + flagged[1:]
        out.append({"icon": "doc", "text": text + ".", "href": "/invoices"})
    a = (fee_audit or {}).get("audit")
    if a and a.get("checked"):
        if a.get("overcharged"):
            text = (f"Found {_plural(len(a['overcharged']), 'overcharged card payment')} (£{a.get('extra_total', 0):.2f}) "
                    f"among the {a['checked']:,} I checked against your fee summary.")
        else:
            text = f"Checked {a['checked']:,} card payments against your fee summary: every fee matched."
        out.append({"icon": "pound", "text": text, "href": "/sales#fees"})
    if memo and memo.get("insights"):
        fixed = sum(len(board_fixes(i.get("first_problems"))) for i in memo["insights"])
        read = "Read the whole business in one go"  # the token count stays in board_memos, not on the owner's page
        text = (f"{read} and caught {_plural(fixed, 'wrong number')} in my own memo before showing you." if fixed
                else f"{read}; every number in the memo checked against your records.")
        out.append({"icon": "bulb", "text": text, "href": "/board"})
    o = (record or {}).get("overall")
    if o and o.get("days"):
        if o["adjusted_error"] > o["baseline_error"]:
            text = (f"Graded my own forecasts: my weather adjustments made them worse ({o['baseline_error']}% → "
                    f"{o['adjusted_error']}% off), so I learned from it and now adjust by ±{round(record['cap'])}% at most.")
        else:
            text = (f"Graded my own forecasts: my adjustments helped ({o['baseline_error']}% → {o['adjusted_error']}% off) "
                    f"on {o['helped']} of {o['days']} days.")
        out.append({"icon": "chart", "text": text, "href": "/sales#record"})
    return out


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


PLURAL_UNITS = {"litre": "litres", "bag": "bags", "slice": "slices"}
WINDY_KMH = 30  # max wind speed (Open-Meteo, 10 m) from which the awning, umbrellas and A-boards need securing


def qty(amount, unit: str = "", up: bool = False) -> str:
    """Stock as the owner says it: a whole number with its unit. '3 kg', '12 litres', '1 litre', '250 g', '40'.
    up=True rounds up (an amount to order or a shortfall: rounding 0.4 down would hide it)."""
    n = max(0, math.ceil(amount - 1e-9) if up else round(amount or 0))
    unit = (unit or "").strip()
    if unit in ("", "unit", "each"):
        return f"{n}"
    return f"{n} {PLURAL_UNITS.get(unit, unit) if n != 1 else unit}"


def wind_warning(wx: Optional[dict]) -> Optional[str]:
    """'Windy: up to 34 km/h, secure the awning and A-boards.' when the day's forecast wind reaches WINDY_KMH."""
    kmh = (wx or {}).get("wind_max_kmh")
    if kmh is None or kmh < WINDY_KMH:
        return None
    return f"Windy: up to {round(kmh)} km/h, secure the awning and A-boards."


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


def _refresh_weather() -> None:
    try:
        days, ttl = {d["date"]: d for d in weather.forecast(PITCHES[0], days=7, timeout=WEATHER_TIMEOUT)}, WEATHER_TTL
    except Exception:  # the page must render without weather
        days, ttl = None, WEATHER_RETRY
    with _weather_lock:
        if days is not None:
            _weather["days"] = days
        _weather.update(at=time.monotonic(), ttl=ttl, refreshing=False)


def weather_by_date() -> Dict[str, dict]:
    """The first pitch's 7-day forecast keyed by date; {} when Open-Meteo can't be reached.
    Never makes a page wait on a refresh: stale weather is served while a background thread fetches new weather.
    Only the very first fetch (nothing cached yet) is inline, and it gives up after WEATHER_TIMEOUT seconds."""
    with _weather_lock:
        days = _weather["days"]
        if time.monotonic() - _weather["at"] < _weather["ttl"]:
            return days
        if days:
            if not _weather.get("refreshing"):
                _weather["refreshing"] = True
                threading.Thread(target=_refresh_weather, name="weather-refresh", daemon=True).start()
            return days
    _refresh_weather()
    return _weather["days"]


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


TIMELINE_HIDDEN = {"chat_message"}  # the dashboard chat's own copy of an exchange; "Answered your question" covers it


def timeline(entries: List[dict], now: datetime) -> List[dict]:
    out = []
    for e in entries:
        if e.get("kind") in TIMELINE_HIDDEN:
            continue
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
OPPORTUNITY = re.compile(r"\b(partner(ship)?s?|collab\w*|opportunit\w*|pop-?ups?|sponsor\w*)\b", re.IGNORECASE)
AUTOMATED = re.compile(r"(no-?reply|donotreply|mailer-daemon|notifications?)@", re.IGNORECASE)


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
            if subject and OPPORTUNITY.search(subject) and not AUTOMATED.search(e.get("from") or "") and subject.lower() not in seen:
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


def payment_checks(pending: List[dict], invoices: List[dict]) -> Dict[str, dict]:
    """For each pending supplier payment: where its invoice came from and whether this is the first invoice we've ever had
    from that supplier. Anyone can email an invoice in, so a first-time sender gets a visible warning before Pay."""
    by_id = {str(i.get("_id")): i for i in invoices}
    seen: Dict[str, int] = {}
    for i in invoices:
        key = i.get("counterparty_key") or (i.get("counterparty") or "").lower()
        seen[key] = seen.get(key, 0) + 1
    out = {}
    for a in pending:
        if a.get("action_type") != "supplier_payment":
            continue
        inv = by_id.get(str(a.get("target_id"))) or {}
        key = inv.get("counterparty_key") or (inv.get("counterparty") or "").lower()
        read_by_ai = "verification_issues" in inv  # seed/demo invoices weren't read from a document
        out[str(a["_id"])] = {
            "source": inv.get("source") if read_by_ai else "demo",
            "new_sender": bool(inv) and read_by_ai and seen.get(key, 0) <= 1,
            "needs_review": bool(inv.get("needs_review")),
            "from_email": inv.get("counterparty_email"),
        }
    return out


TODO_SHOWN = 6
TASK_LINKS = {"prep_day": "/sales", "price_rise": "/invoices", "price_match": "/invoices", "fee_overcharge": "/sales",
              "review_invoice": "/invoices"}


def todo_list(pending: List[dict], review: List[dict], tasks: List[dict], stock: dict, mine: List[dict] = ()) -> dict:
    """The owner's to-do list on every page: what needs a yes first (most overdue first), then invoices to check, the top
    priorities and stock running out. Each item links to where it's done. Display only: nothing here changes data."""
    items = []
    for a in sorted(pending, key=lambda a: -(a.get("days_overdue") or 0)):
        verb = "Pay" if a.get("action_type") == "supplier_payment" else "Chase"
        late = f" · {a['days_overdue']} days overdue" if a.get("days_overdue") else ""
        items.append({"kind": "approve", "text": f"{verb} {a.get('counterparty') or a['title']}",
                      "detail": f"£{a['amount']:,.2f}{late} · reply “approve {a['ref']}”", "href": f"/approvals#a{a['ref']}"})
    for inv in review:
        items.append({"kind": "check", "text": f"Check {inv.get('invoice_number')} from {inv.get('counterparty')}",
                      "detail": (inv.get("verification_issues") or ["didn't add up when I read it"])[0], "href": "/invoices"})
    for t in mine:  # the owner's own to-dos (backend/todos.py), ticked off on Home
        items.append({"kind": "do", "text": f"T{t['ref']} {t['text']}", "detail": f"by {t['due']} · reply “done T{t['ref']}”"
                      if t.get("due") else f"reply “done T{t['ref']}”", "href": "/#todos"})
    for t in tasks:
        if t.get("kind") in ("approval", "review_invoice", "owner_todo"):
            continue  # already listed above
        items.append({"kind": "do", "text": t["title"], "detail": t.get("reason") or "", "href": TASK_LINKS.get(t.get("kind"), "/")})
    for s in stock.get("low", [])[:2]:
        left = "probably out" if s.get("out") else f"about {round(s.get('cover') or 0)} days left"
        items.append({"kind": "stock", "text": f"Order {s['name'].lower()}",
                      "detail": f"{left} · about {qty(s['order'], s['unit'], up=True)}" if s.get("order") else left, "href": "/inventory"})
    return {"items": items[:TODO_SHOWN], "more": max(0, len(items) - TODO_SHOWN), "count": len(items)}


HIGHLIGHT = re.compile(
    r"£\s?\d[\d,]*(?:\.\d{1,2})?(?:k|m)?"                                                     # money
    r"|[+−-]?\d+(?:\.\d+)?%"                                                                   # percentages
    r"|\b\d+\s+days?\s+(?:overdue|late|left)\b|\bprobably out\b|\boverdue\b"                    # urgency
    r"|\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\b",  # dates
    re.IGNORECASE)


def highlight(text) -> Markup:
    """Long text with its key facts (money, percentages, deadlines, dates) marked so they can be found at a glance.
    Escapes first, so text from invoices or Gemini can never inject markup."""
    safe = str(escape(text or ""))
    return Markup(HIGHLIGHT.sub(lambda m: f'<mark class="hl">{m.group(0)}</mark>', safe))
