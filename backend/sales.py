"""Recording till data: the hand-off from POS inputs (Jade) to the database (Sunil).

Input code (e.g. backend/inputs/pos/sync.py) passes orders and stock counts **exactly as the POS
connector returns them** (see the format at the top of the POS section in schema.py):

    from backend.inputs.pos.client import iter_sales, list_stock_counts
    from backend.sales import record_orders, record_stock_count
    result = record_orders(iter_sales(since="2026-10-01"))   # SyncResult(new=…, duplicates=…, skipped=…, errors=[…])
    for count in list_stock_counts():
        record_stock_count(count)

Safe to run any number of times: an order or count that's already stored is skipped, so a sync
can simply re-read a window that overlaps the last one. Never raises on bad data: bad orders are
reported in `errors` and the rest are still recorded.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from backend.db import activity, schema
from backend.db.client import check_real_data_allowed, get_db

BUSINESS_ID = "gaia-coffee"
LONDON = ZoneInfo("Europe/London")  # the truck's time zone: timestamps without an offset are London time
TOLERANCE = 0.01
CATEGORIES = {  # GAIA's menu SKUs → category (unknown SKUs stay None until someone sets them)
    "ESP": "coffee", "AME": "coffee", "LON": "coffee", "COR": "coffee", "FLW": "coffee", "CAP": "coffee", "LAT": "coffee",
    "IAM": "cold", "ILA": "cold", "IMA": "cold", "MAT": "tea", "TEA": "tea", "PAT": "food", "CKI": "food", "GCA": "food",
}


@dataclass
class SyncResult:
    new: int = 0  # orders recorded now
    duplicates: int = 0  # already stored (fine: overlapping sync windows)
    skipped: int = 0  # not a completed, paid sale (cancelled, refunded, ...)
    errors: List[str] = field(default_factory=list)  # "<order_id>: what's wrong"; those orders were NOT recorded
    revenue: float = 0.0  # total of the new orders, GBP

    @property
    def message(self) -> str:
        parts = [f"{self.new} new order(s) (£{self.revenue:,.2f})", f"{self.duplicates} already recorded"]
        if self.skipped:
            parts.append(f"{self.skipped} skipped (not completed/paid)")
        if self.errors:
            parts.append(f"{len(self.errors)} rejected")
        return "Till sync: " + ", ".join(parts) + "."


def _utc(value) -> datetime:
    """ISO string or datetime → aware UTC datetime. No offset → London time (like the POS connector)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(dt, datetime):
        raise ValueError(f"not a timestamp: {value!r}")
    return (dt if dt.tzinfo else dt.replace(tzinfo=LONDON)).astimezone(timezone.utc)


def _num(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    return float(value)


def _parse(order: dict, now: datetime, synthetic: bool = True) -> Optional[tuple]:
    """Validate one POS order → (order_doc, sale_docs), None to skip it, or raise ValueError(reason)."""
    if order.get("order_status", "completed") != "completed" or (order.get("payment") or {}).get("status", "paid") != "paid":
        return None
    oid = str(order.get("order_id") or "").strip()
    if not oid:
        raise ValueError("missing order_id")
    placed = _utc(order.get("placed_at"))
    if placed > now:
        raise ValueError(f"placed_at {placed.isoformat()} is in the future")
    items = order.get("items") or []
    if not items:
        raise ValueError("no items")
    location = order.get("location_id") or "unknown"

    sales, items_total, count = [], 0.0, 0.0
    for n, it in enumerate(items, 1):
        sku = str(it.get("sku") or "").strip()
        if not sku:
            raise ValueError(f"line {n}: missing sku")
        qty, unit = _num(it.get("quantity"), f"line {n} quantity"), _num(it.get("unit_price_gbp"), f"line {n} unit_price_gbp")
        if qty <= 0:
            raise ValueError(f"line {n}: quantity {qty:g} (refunds aren't supported yet)")
        if unit < 0:
            raise ValueError(f"line {n}: negative price")
        mods = [{"name": m.get("name") or "", "value": m.get("value") or "", "price": _num(m.get("price_gbp", 0) or 0, "modifier price")}
                for m in (it.get("modifiers") or [])]
        line_total = _num(it.get("line_total_gbp", qty * unit), f"line {n} line_total_gbp")
        if abs(line_total - qty * (unit + sum(m["price"] for m in mods))) > TOLERANCE and abs(line_total - qty * unit) > TOLERANCE:
            raise ValueError(f"line {n}: line total {line_total:.2f} doesn't match {qty:g} × {unit:.2f}")
        items_total += line_total
        count += qty
        sales.append({"ts": placed, "meta": {"business_id": BUSINESS_ID, "sku": sku, "location_id": location},
                      "order_id": oid, "line_no": n, "name": it.get("name") or sku, "variant": it.get("variant"),
                      "qty": qty, "unit_price": unit, "modifiers": mods, "line_total": round(line_total, 2), "synthetic": synthetic})

    subtotal = _num(order.get("subtotal_gbp", items_total), "subtotal_gbp")
    discount = _num(order.get("discount_gbp", 0) or 0, "discount_gbp")
    total = _num(order.get("total_gbp", subtotal - discount), "total_gbp")
    if abs(items_total - subtotal) > TOLERANCE or abs(subtotal - discount - total) > TOLERANCE:
        raise ValueError(f"totals don't add up (lines {items_total:.2f}, subtotal {subtotal:.2f}, discount {discount:.2f}, total {total:.2f})")

    doc = {"_id": f"{BUSINESS_ID}:{oid}", "business_id": BUSINESS_ID, "order_id": oid, "placed_at": placed,
           "location_id": location, "source": order.get("source") or "pos", "fulfilment": order.get("fulfilment"),
           "subtotal": round(subtotal, 2), "discount": round(discount, 2), "total": round(total, 2), "item_count": count,
           "payment_method": (order.get("payment") or {}).get("method"), "synthetic": synthetic, "synced_at": now}
    return doc, sales


def _update_products(db, sales: List[dict], synthetic: bool = True) -> None:
    """Keep the menu in step with what the till actually sells (latest price wins)."""
    for s in sorted(sales, key=lambda s: s["ts"]):
        sku = s["meta"]["sku"]
        update = {"$set": {"name": s["name"], "price": s["unit_price"], "business_id": BUSINESS_ID, "synthetic": synthetic},
                  "$min": {"first_sold_at": s["ts"]}, "$max": {"last_sold_at": s["ts"]},
                  "$setOnInsert": {"category": CATEGORIES.get(sku)}}
        if s["variant"]:
            update["$addToSet"] = {"variants": s["variant"]}
        db[schema.PRODUCTS].update_one({"_id": sku}, update, upsert=True)


def record_orders(orders: Iterable[dict], origin: str = "POS", now: Optional[datetime] = None,
                  synthetic: bool = True) -> SyncResult:
    """Store completed, paid POS orders (idempotent). One activity-log line per call, not per order.
    `now` pins the clock (demo replays, tests), like PosClient(now=...): orders after it are refused as future.
    synthetic=False (a real till, e.g. Square production) is refused in the shared demo database."""
    db = get_db()
    if not synthetic:
        check_real_data_allowed(db)
    now = _utc(now) if now else datetime.now(timezone.utc)
    result, new_sales = SyncResult(), []
    for order in orders:
        if not isinstance(order, dict):
            result.errors.append(f"?: not an order ({type(order).__name__})")
            continue
        try:
            parsed = _parse(order, now, synthetic)
        except (ValueError, TypeError) as e:
            result.errors.append(f"{order.get('order_id') or '?'}: {e}")
            continue
        if parsed is None:
            result.skipped += 1
            continue
        doc, sales = parsed
        try:
            db[schema.ORDERS].insert_one(doc)  # claims the order: a second sync stops here
        except DuplicateKeyError:
            result.duplicates += 1
            continue
        try:
            db[schema.SALES].insert_many(sales)
        except Exception as e:  # don't leave an order without its lines: release it so the next sync retries
            db[schema.ORDERS].delete_one({"_id": doc["_id"]})
            result.errors.append(f"{doc['order_id']}: couldn't save lines ({type(e).__name__}); will retry next sync")
            continue
        result.new += 1
        result.revenue += doc["total"]
        new_sales += sales
    if new_sales:
        _update_products(db, new_sales, synthetic)
    if result.new or result.errors:
        activity.log("sales_synced", f"{result.message} [{origin}]", new=result.new, duplicates=result.duplicates,
                     skipped=result.skipped, errors=result.errors[:10], revenue=round(result.revenue, 2))
    return result


def record_stock_count(count: dict) -> bool:
    """Store one physical stock count (idempotent by its count_id). Returns True if it was new."""
    cid = str(count.get("count_id") or "").strip()
    if not cid or count.get("record_type", "physical_inventory_count") != "physical_inventory_count":
        raise ValueError("not a physical stock count (missing count_id)")
    kind = "opening" if "OPENING" in cid.upper() else "closing" if "CLOSING" in cid.upper() else "other"
    items = [{"sku": i["sku"], "item_name": i.get("item_name") or i["sku"], "unit": i.get("unit") or "",
              "qty": _num(i.get("counted_quantity"), f"{i['sku']} counted_quantity")} for i in count.get("items") or []]
    doc = {"_id": cid, "business_id": BUSINESS_ID, "location_id": count.get("location_id") or "unknown",
           "counted_at": _utc(count.get("counted_at")), "kind": kind, "method": count.get("count_method"),
           "items": items, "synthetic": True}
    try:
        get_db()[schema.STOCK_COUNTS].insert_one(doc)
        return True
    except DuplicateKeyError:
        return False


def latest_stock() -> Optional[dict]:
    """The most recent stored stock count, or None."""
    return get_db()[schema.STOCK_COUNTS].find_one({"business_id": BUSINESS_ID}, sort=[("counted_at", -1)])
