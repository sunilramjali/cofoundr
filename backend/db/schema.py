"""CoFoundr shared data contract: MongoDB collections and document shapes.

Owner: Sunil. Both backend and web app code import from here.
Any change to a shape here must be announced to your partner (and logged) before merging.
"""
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


class Business(TypedDict):
    _id: str  # slug, e.g. "gaia-coffee"
    name: str
    business_type: str
    location: str
    currency: str
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
    reminders_sent: int  # escalation level for receivables
    synthetic: bool
    created_at: datetime


class Approval(TypedDict):
    business_id: str
    action_type: str  # e.g. "invoice_reminder", "supplier_payment"
    target_id: str  # _id of the related document
    draft: str
    status: Literal["pending", "approved", "edited", "rejected"]
    decided_via: Optional[Literal["dashboard", "whatsapp"]]
    created_at: datetime
    decided_at: Optional[datetime]


# TODO(Sunil, MVP2): Product, Sale, Event, Forecast, Task shapes
