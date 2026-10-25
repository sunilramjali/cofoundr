"""CoFoundr shared data contract: MongoDB collections and document shapes.

Owner: Sunil. Both backend and web app code import from here.
Any change to a shape here must be announced to your partner (and logged) before merging.
"""
import re
from datetime import datetime
from typing import List, Literal, NotRequired, Optional, TypedDict

DB_NAME = "cofoundr-db"

# Collection names (single source of truth: never hard-code these elsewhere)
BUSINESSES = "businesses"
PRODUCTS = "products"
SALES = "sales"
INVOICES = "invoices"
EVENTS = "events"  # things happening near the truck's pitches (Ticketmaster), one doc per event
WEATHER = "weather"  # daily weather forecast per pitch (Open-Meteo), one doc per pitch per London day
TRANSPORT = "transport"  # TfL disruptions that hit a station near a pitch during trading hours, one doc per disruption
FORECASTS = "forecasts"
TASKS = "tasks"
APPROVALS = "approvals"
AGENT_LOG = "agent_log"  # every agent action / decision, for the "clear record" judging criterion
CURSORS = "cursors"  # per-consumer read position in agent_log for /updates, _id = "<business_id>:<consumer>"
COUNTERS = "counters"  # atomic sequences, e.g. _id = "<business_id>:approval_ref"
ORDERS = "orders"  # one doc per till order (idempotency + order totals); its lines live in SALES
STOCK_COUNTS = "stock_counts"  # physical ingredient counts on the truck (beans, milk, cups), one doc per count
FORECAST_REVIEWS = "forecast_reviews"  # past forecasts graded against actual takings (backend/ai/review.py)
LESSONS = "lessons"  # what the forecaster learned from its grades; the active ones go into the next forecast
FEE_AUDITS = "fee_audits"  # card provider's fee summary read by Gemini + every payment checked against it (backend/ai/fees.py)
BOARD_MEMOS = "board_memos"  # Gemini's checked board memo, one per business per day (backend/ai/board.py)
PAYMENTS = "payments"  # card payments from a card provider with no item detail (EVO CSV, SumUp), card data stripped


def counterparty_key(name: Optional[str]) -> str:
    """Normalised name used to tell invoices apart: invoice numbers are only unique per supplier/customer.
    "Eastside Dairy Co." and "eastside dairy co" → "eastsidedairyco"."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def invoice_search_text(doc: dict) -> str:
    """One line describing an invoice, for Atlas Search and Automated Embedding (Voyage) to index.
    "Eastside Dairy Co | supplier bill (we pay) | EDC-7781 | Whole milk, 2L; Oat milk, 1L"."""
    kind = "customer invoice (owed to us)" if doc.get("direction") == "receivable" else "supplier bill (we pay)"
    items = "; ".join(li.get("description", "") for li in doc.get("line_items") or [])
    return " | ".join([doc.get("counterparty") or "", kind, doc.get("invoice_number") or "", items])


class AgentLogEntry(TypedDict):
    business_id: str
    kind: str  # e.g. "invoice_extracted", "overdue_check", "approval_decided", "brief_served"
    summary: str  # one human-readable line
    actor: Literal["agent", "owner"]
    detail: dict
    at: datetime


class Business(TypedDict):
    _id: str  # slug, e.g. "gaia-coffee"
    name: str
    business_type: str
    location: str
    currency: str
    # counterparty_key()s the owner is loyal to: CoFoundr still reports their price rises, but never suggests
    # alternatives to them (backend/db/search.py). Missing = none.
    preferred_suppliers: List[str]
    synthetic: bool  # True for all demo data
    created_at: datetime


class LineItem(TypedDict):
    description: str
    qty: float
    unit_price: float


class Invoice(TypedDict):
    business_id: str
    invoice_number: str
    # receivable = a customer owes the business (CoFoundr chases it)
    # payable    = the business owes a supplier (CoFoundr reminds the owner to pay)
    direction: Literal["receivable", "payable"]
    counterparty: str  # customer or supplier name
    counterparty_key: str  # counterparty_key(counterparty); unique with (business_id, direction, invoice_number)
    counterparty_email: Optional[str]
    amount: float  # must equal sum(qty * unit_price) of line_items
    currency: str
    issue_date: datetime
    due_date: datetime
    line_items: List[LineItem]
    status: Literal["open", "overdue", "paid"]
    paid_at: Optional[datetime]
    source: Literal["whatsapp_photo", "pdf", "email", "manual"]
    extraction_confidence: float  # 0-1, from Gemini verification loop
    needs_review: bool  # True when verification fails: owner checks, agent doesn't guess
    verification_issues: List[str]  # what failed; empty when clean (seeded docs omit it)
    extraction_notes: Optional[str]  # Gemini notes on unclear values
    reminders_sent: int  # escalation level for receivables
    search_text: str  # invoice_search_text(doc): what Atlas Search + Automated Embedding index (backend/db/search.py)
    possible_duplicate_of: NotRequired[str]  # _id of a stored invoice this one probably repeats ("EDC 7781" = "EDC-7781")
    synthetic: bool
    created_at: datetime


class Approval(TypedDict):
    business_id: str
    ref: int  # short number the owner uses on WhatsApp: "approve 3"
    action_type: Literal["invoice_reminder", "supplier_payment"]
    target_id: str  # str(_id) of the related invoice
    invoice_number: str
    counterparty: str
    amount: float
    days_overdue: int
    title: str  # one line for the queue, e.g. "Chase X: £450.00, 3 days overdue"
    reason: str  # why the agent proposes this (shown to the owner)
    escalation_level: Optional[int]  # 0 friendly, 1 firm, 2 final (reminders only)
    to_email: Optional[str]
    draft_subject: Optional[str]
    draft: str  # email body, or the payment instruction
    drafted_by: Literal["gemini", "template", "rule"]
    # cancelled = the system withdrew it (invoice paid/removed before a decision); nothing was sent
    status: Literal["pending", "approved", "edited", "rejected", "cancelled"]
    decided_via: Optional[Literal["dashboard", "whatsapp", "system"]]
    created_at: datetime
    decided_at: Optional[datetime]
    synthetic: bool


# ---------------------------------------------------------------- POS: products, orders, sales, stock (MVP2)
# INPUT format = Jade's POS export (backend/inputs/pos/data, "illustrative_pos_orders_export_v2"):
#   {"order_id", "placed_at" (ISO, with offset), "location_id", "source", "fulfilment",
#    "items": [{"sku", "name", "variant", "quantity", "unit_price_gbp", "modifiers": [{"name","value","price_gbp"}],
#               "line_total_gbp"}], "subtotal_gbp", "discount_gbp", "total_gbp",
#    "payment": {"method", "status", "payment_reference"}, "order_status"}
# Inputs pass orders in that format to backend.sales.record_orders(); only backend.sales writes these collections.

class Product(TypedDict):
    """A menu item, kept up to date from the till: one per POS SKU."""
    _id: str  # POS SKU, e.g. "FLW"
    business_id: str
    name: str  # "Flat White"
    category: Optional[Literal["coffee", "tea", "cold", "food", "other"]]
    price: float  # latest unit price seen at the till, GBP
    variants: List[str]  # e.g. ["Almond Croissant", "Pain au Chocolat"] for "Fresh Pastry"
    first_sold_at: datetime
    last_sold_at: datetime
    synthetic: bool


class Order(TypedDict):
    _id: str  # "<business_id>:<order_id>": recording the same order twice is a no-op
    business_id: str
    order_id: str  # the till's id, e.g. "GAIA-20261002-001358"
    placed_at: datetime  # UTC
    location_id: str  # e.g. "GAIA-TRUCK-01"
    source: str  # e.g. "in_person_pos"
    fulfilment: Optional[str]
    subtotal: float
    discount: float
    total: float
    item_count: int  # sum of quantities
    payment_method: Optional[str]
    synthetic: bool
    synced_at: datetime


class Modifier(TypedDict):
    name: str  # "Milk", "Extra Espresso Shot"
    value: str  # "Oat Milk", "1 additional shot"
    price: float


class SaleMeta(TypedDict):
    business_id: str
    sku: str
    location_id: str


class Sale(TypedDict):
    """SALES is a MongoDB **time-series** collection (timeField "ts", metaField "meta"): one doc per order line."""
    ts: datetime  # = Order.placed_at
    meta: SaleMeta
    order_id: str
    line_no: int
    name: str
    variant: Optional[str]
    qty: float
    unit_price: float
    modifiers: List[Modifier]
    line_total: float
    synthetic: bool


class StockItem(TypedDict):
    sku: str  # ingredient SKU, e.g. "INV-001" (not a menu SKU)
    item_name: str  # "Espresso Coffee Beans"
    unit: str  # "kg", "litre", "each"
    qty: float


class StockCount(TypedDict):
    _id: str  # the count's id, e.g. "GAIA-COUNT-OPENING-20261002"
    business_id: str
    location_id: str
    counted_at: datetime  # UTC
    kind: Literal["opening", "closing", "other"]
    method: Optional[str]
    items: List[StockItem]
    synthetic: bool


# ---------------------------------------------------------------- card payments (MVP2)
# For card providers that know how much was paid but not what was sold (EVO's CSV export, SumUp). A provider that
# knows the items (Square) goes through the POS format above instead. Privacy (docs/privacy.md): only the fields below
# are ever stored. No card number (masked or not), cardholder name, auth code, card-network reference, card token or
# issuer country; the provider's transaction id is stored only as a salted hash, for de-duplication.
#
# INPUT format (inputs → backend.payments.record_payments), one dict per payment:
#   {"source": "evo" | "sumup", "source_id": str (provider's id: hashed, never stored), "paid_at": ISO timestamp with
#    offset, or "YYYY-MM-DD" when the provider only gives the date, "amount_gbp": float, "fee_gbp": float | None,
#    "currency": "GBP", "kind": "sale" | "refund" | "other", "status": "successful" | <anything else = skipped>,
#    "card_product": "debit" | "credit" | None, "card_category": "consumer" | "corporate" | None,
#    "region": "domestic" | "eea" | "international" | None, "brand": "Visa" | ... | None, "entry_mode": str | None}
# Any other key is ignored. Real data (synthetic=False) is refused in the shared demo database (DB_NAME).

class Payment(TypedDict):
    _id: str  # "<business_id>:<source>:<id_hash>": importing the same payment twice is a no-op
    business_id: str
    source: Literal["evo", "sumup"]
    id_hash: str  # HMAC-SHA256 of the provider's transaction id with PAYMENT_ID_SALT (first 24 hex chars)
    date: str  # London day, "2026-10-24"
    paid_at: Optional[datetime]  # UTC; None when the provider only gives the date (EVO's CSV)
    amount: float  # GBP, always positive; `kind` says whether it came in or went back
    fee: Optional[float]  # what the provider charged for it, GBP (EVO: MSC); None = not given
    kind: Literal["sale", "refund"]
    card_product: Optional[Literal["debit", "credit"]]
    card_category: Optional[Literal["consumer", "corporate"]]
    region: Optional[Literal["domestic", "eea", "international"]]
    brand: Optional[str]
    entry_mode: Optional[str]
    synthetic: bool
    imported_at: datetime


# ---------------------------------------------------------------- forecast reviews + lessons (MVP2)

class ForecastReview(TypedDict):
    _id: str  # "<business_id>:<live|backtest>:<YYYY-MM-DD>"
    business_id: str
    date: str
    weekday: str
    mode: Literal["live", "backtest"]  # backtest = replayed with observed weather, Gemini saw only earlier takings
    expected: float  # baseline
    final: float  # after Gemini's adjustment
    actual: float
    baseline_error_pct: Optional[float]
    final_error_pct: Optional[float]
    adjustment_pct: Optional[float]
    factors: List[dict]  # [{"kind", "label", "effect_pct"}]
    helped: Optional[bool]  # measured by code: adjustment moved the forecast closer to actual; None = no adjustment
    graded_at: datetime
    synthetic: bool


class ForecastLesson(TypedDict):
    business_id: str
    text: str  # one sentence, goes into the next forecast's prompt while active
    about: Literal["weather", "event", "transport", "overall"]
    direction: Literal["adjust_less", "keep", "adjust_more"]  # checked against `measured`
    evidence_dates: List[str]
    measured: dict  # {"days", "baseline_error", "adjusted_error", "helped", "dates"} it was checked against
    active: bool  # a newer set of lessons deactivates the old one
    created_at: datetime
    synthetic: bool


# ---------------------------------------------------------------- board memo (MVP2)

class MemoClaim(TypedDict):
    kind: str  # sales_revenue | sales_units | order_count | supplier_unit_price | invoice_amount | owed_to_us | owed_by_us
    value: float  # what Gemini said
    actual: Optional[float]  # what MongoDB says (None = couldn't be checked, see `why`)
    ok: bool
    why: Optional[str]
    says: str  # the phrase in the finding it backs
    # plus the parameters Gemini gave: start, end, sku, modifier, supplier, invoice_number, item


class MemoInsight(TypedDict):
    title: str
    finding: str
    action: str  # a suggestion: any money action still goes through approvals
    impact_gbp_per_month: Optional[float]  # Gemini's estimate, always shown as an estimate
    # verified = all claims matched; corrected = matched after one recheck; withdrawn = Gemini dropped it when shown
    # the real numbers; failed = still wrong (shown as failed, never silently fixed)
    status: Literal["verified", "corrected", "withdrawn", "failed"]
    claims: List[MemoClaim]
    problems: List[str]  # what's still wrong (failed / withdrawn)
    first_problems: List[str]  # what the recheck fixed (corrected)


class BoardMemo(TypedDict):
    _id: str  # "<business_id>:<YYYY-MM-DD>", latest run that day wins
    business_id: str
    date: str
    headline: str
    insights: List[MemoInsight]
    attempts: int
    model: str
    read: dict  # {"tokens", "sales_lines", "invoices"}: how much Gemini read in one go
    generated_at: datetime
    synthetic: bool


# ---------------------------------------------------------------- forecast (MVP2)

class ProductForecast(TypedDict):
    sku: str
    name: str
    expected_qty: float  # units expected that day


class ForecastFactor(TypedDict):
    kind: Literal["weather", "event", "transport"]
    ref: str  # the condition it's based on: "weather:<date>", an Event _id or a TransportDisruption _id
    label: str  # "Light rain", "Central line severe delays at Bethnal Green"
    effect_pct: float  # this factor's share of the day's pct
    why: str  # Gemini's short explanation


class ForecastAdjustment(TypedDict):
    """Gemini's change to the baseline from the day's weather, events and transport. Only stored after the checks in
    backend.ai.forecast.check() pass (factors exist, add up, within limits, sensible direction, reason states the %)."""
    pct: float  # e.g. +12.0
    reason: str  # one line for the owner, e.g. "-18% Mon: heavy rain all day"
    confidence: Literal["high", "medium", "low"]
    by: Literal["gemini"]
    sources: List[str]  # kinds of factor used, e.g. ["transport", "weather"]; [] = looked, no change
    factors: List[ForecastFactor]
    attempts: int  # 2 = accepted after Gemini fixed what the checks rejected


class Forecast(TypedDict):
    """One doc per business per day ahead; recomputed by backend.ai.forecast.run(), latest run wins."""
    _id: str  # "<business_id>:<YYYY-MM-DD>"
    business_id: str
    date: str  # London calendar date, "YYYY-MM-DD"
    weekday: str  # "Saturday"
    trading: bool  # False on days GAIA doesn't trade (then everything below is 0)
    expected_revenue: float  # baseline, GBP
    expected_orders: float
    low_revenue: float  # range from the same weekdays' history (min..max of the lookback)
    high_revenue: float
    basis: str  # human-readable, e.g. "average of the last 6 Saturdays, leaving out 10 Oct (far below a usual Saturday)"
    left_out: List[str]  # unusual past days not averaged (closed early, rained off, card machine down), "YYYY-MM-DD"
    products: List[ProductForecast]  # top sellers expected that day
    adjustment: Optional[ForecastAdjustment]  # None until Gemini adjusts it
    final_revenue: float  # baseline × (1 + pct/100), or the baseline when not adjusted
    generated_at: datetime
    synthetic: bool


# ---------------------------------------------------------------- priorities (MVP2)

class Task(TypedDict):
    """One thing the owner should do, ranked by backend.ai.prioritise.run(). Each run replaces the whole list.
    Tasks are gathered from MongoDB (never invented by Gemini); Gemini only orders them and says why."""
    _id: str  # "<business_id>:<kind>:<key>", stable across runs
    business_id: str
    # price_rise = a supplier charged more than last time; price_match = another supplier is cheaper, ask to match
    # fee_overcharge = the card provider charged more than its own fee summary allows (backend/ai/fees.py)
    kind: Literal["approval", "review_invoice", "prep_day", "price_rise", "price_match", "fee_overcharge"]
    title: str  # one line, e.g. "Approve #3: chase Hackney Hub, £450.00, 12 days overdue"
    facts: str  # what the ranking was based on
    target_id: Optional[str]  # str(_id) of the approval / invoice / forecast it's about
    approval_ref: Optional[int]  # for kind "approval": the owner replies "approve <ref>"
    amount: Optional[float]  # GBP at stake (or expected revenue for prep_day)
    rank: int  # 1 = do first
    impact: Literal["high", "medium", "low"]
    reason: str  # one line, shown to the owner
    ranked_by: Literal["gemini", "rule"]  # "rule" = Gemini unavailable or skipped this task
    ranked_at: datetime
    synthetic: bool


# ---------------------------------------------------------------- weather + events around the pitches (MVP2)
# INPUT formats = Jade's backend/inputs/weather.py and events.py (see backend/inputs/README.md), passed as-is to
# backend.conditions.record_weather() / record_events(); only backend.conditions writes these collections.
# Real public data (not synthetic), so synthetic=False.

class WeatherDay(TypedDict):
    """The latest forecast for one pitch on one day: a re-sync overwrites it (forecasts improve as the day nears)."""
    _id: str  # "<business_id>:<pitch_id>:<date>"
    business_id: str
    pitch_id: str  # backend/inputs/pitches.py id, e.g. "broadway-market"
    date: str  # London day, "2026-10-25"
    summary: str  # "Light drizzle"
    weather_code: Optional[int]  # WMO code; None = no data
    temp_max_c: Optional[float]
    temp_min_c: Optional[float]
    precip_mm: Optional[float]
    precip_chance_pct: Optional[float]
    wind_max_kmh: Optional[float]
    source: str  # "open-meteo"
    fetched_at: datetime  # when the source produced this forecast
    updated_at: datetime  # when we last stored a change
    synthetic: bool


class Event(TypedDict):
    """One event near a pitch. Never deleted: an event that drops out of a later fetch for the same window and pitch
    gets status "removed" (cancelled or moved), so the forecast stops counting it."""
    _id: str  # "<business_id>:<source>:<source_id>"
    business_id: str
    source: str  # "ticketmaster"
    source_id: str
    name: str
    category: Optional[str]  # "Music", "Sports", ...
    genre: Optional[str]
    date: str  # London day, "2026-10-25"
    starts_at: Optional[datetime]  # UTC; None = time to be announced
    status: str  # the source's status ("onsale", "rescheduled", ...) or "removed"
    venue: Optional[str]
    lat: Optional[float]
    lon: Optional[float]
    distance_km: Optional[float]  # to the nearest pitch
    pitch_id: str  # nearest pitch (an event found near two pitches is stored once)
    url: Optional[str]
    search_text: str  # "name | category, genre | venue": for Automated Embedding (event ↔ business matching)
    fetched_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    synthetic: bool


class TransportDisruption(TypedDict):
    """A TfL disruption that hits at least one station near a pitch during trading hours (backend/transport.py decides;
    line-level problems elsewhere on the line are not stored). Never deleted: one that TfL stops listing for a window
    we re-fetched gets state "resolved"."""
    _id: str  # "<business_id>:tfl:<source_id>"
    business_id: str
    source: str  # "tfl"
    source_id: str  # hash of line + status + TfL's text
    pitch_id: str
    line_id: str  # "weaver", "central", ...
    line_name: str
    status: str  # TfL's words: "Part Closure", "Severe Delays", ...
    impact: Literal["closed", "delays", "uncertain"]  # uncertain = TfL's text couldn't be placed on the line's map
    category: Literal["planned", "live"]  # planned works (known days ahead) or live (today)
    reason: str  # TfL's text, shown as-is
    dates: List[str]  # London days it touches during trading hours (07:00-18:00)
    stations: List[str]  # nearby stations it hits, e.g. ["London Fields", "Cambridge Heath"]
    nearest_m: int  # walking distance from the pitch to the nearest affected station
    starts_at: Optional[datetime]
    ends_at: Optional[datetime]
    state: Literal["active", "resolved"]
    fetched_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    synthetic: bool
