"""CoFoundr shared data contract: MongoDB collections and document shapes.

Owner: Sunil. Both backend and app code import from here.
Any change to a shape here must be announced to your partner (and logged) before merging.
"""
from typing import Literal, TypedDict
from datetime import datetime

DB_NAME = "cofoundr"

# Collection names (single source of truth: never hard-code these elsewhere)
BUSINESSES = "businesses"
PRODUCTS = "products"
SALES = "sales"
INVOICES = "invoices"
EVENTS = "events"
FORECASTS = "forecasts"
TASKS = "tasks"
APPROVALS = "approvals"


class LineItem(TypedDict):
    description: str
    qty: float
    unit_price: float


class Invoice(TypedDict):
    business_id: str
    supplier: str
    amount: float
    currency: str
    due_date: datetime
    line_items: list[LineItem]
    status: Literal["open", "overdue", "paid"]
    source: Literal["whatsapp_photo", "pdf", "email", "manual"]
    extraction_confidence: float  # 0-1, from Gemini verification loop


class Approval(TypedDict):
    business_id: str
    action_type: str  # e.g. "invoice_reminder"
    target_id: str  # _id of the related document
    draft: str
    status: Literal["pending", "approved", "edited", "rejected"]
    decided_via: Literal["dashboard", "whatsapp"] | None
    created_at: datetime
    decided_at: datetime | None


# TODO(Sunil): Business, Product, Sale, Event, Forecast, Task shapes
