"""CoFoundr shared data contract: MongoDB collections and document shapes.

Owner: Sunil. Both backend and web app code import from here.
Any change to a shape here must be announced to your partner (and logged) before merging.
"""
import re
from datetime import datetime
from typing import List, Literal, Optional, TypedDict

DB_NAME = "cofoundr-db"

# Collection names (single source of truth: never hard-code these elsewhere)
BUSINESSES = "businesses"
PRODUCTS = "products"
SALES = "sales"
INVOICES = "invoices"
EVENTS = "events"
FORECASTS = "forecasts"
TASKS = "tasks"
APPROVALS = "approvals"
AGENT_LOG = "agent_log"  # every agent action / decision, for the "clear record" judging criterion
CURSORS = "cursors"  # per-consumer read position in agent_log for /updates, _id = "<business_id>:<consumer>"
COUNTERS = "counters"  # atomic sequences, e.g. _id = "<business_id>:approval_ref"
ORDERS = "orders"  # one doc per till order (idempotency + order totals); its lines live in SALES
STOCK_COUNTS = "stock_counts"  # physical ingredient counts on the truck (beans, milk, cups), one doc per count


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
    actor: Literal["agent", "owner", "saint"]
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


# ---------------------------------------------------------------- forecast (MVP2)

class ProductForecast(TypedDict):
    sku: str
    name: str
    expected_qty: float  # units expected that day


class ForecastAdjustment(TypedDict):
    """Gemini's change to the baseline, from weather + events (MVP2, after Jade's weather/events inputs)."""
    pct: float  # e.g. +28.0
    reason: str  # one line, e.g. "+28% Sat: Broadway Market nearby + sunny"
    sources: List[str]  # what it looked at, e.g. ["weather", "events"]


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
    basis: str  # human-readable, e.g. "average of the last 6 Saturdays"
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
    kind: Literal["approval", "review_invoice", "prep_day", "price_rise", "price_match"]
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


# TODO(Sunil, MVP2): Event shape
