"""Square: completed orders with their items, read-only, mapped to the POS format backend.sales.record_orders takes.

Square knows what was sold, so this feeds the same orders/sales collections as the till (per-product forecast and
stock estimates work too). Read-only: only Square's list/search endpoints are called; an OAuth token needs just
ORDERS_READ (+ MERCHANT_PROFILE_READ when SQUARE_LOCATION_ID isn't set).

Privacy: customer ids, fulfilment recipients (names, phones, addresses), notes and card details on tenders are never
copied; only the tender type ("card", "cash") is kept as the payment method (see docs/privacy.md).

Env: SQUARE_ACCESS_TOKEN, SQUARE_ENV=sandbox|production (default sandbox: Square's test data counts as synthetic),
SQUARE_LOCATION_ID (optional: default all locations).

    python -m backend.inputs.square --days 7 --dry-run
    python -m backend.inputs.square --days 30
"""
import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from backend.inputs import fetch

HOSTS = {"sandbox": "https://connect.squareupsandbox.com", "production": "https://connect.squareup.com"}
VERSION = os.environ.get("SQUARE_VERSION", "2025-01-23")  # Square-Version header: pins the API's response shape
PAGE = 500
MAX_PAGES = 200  # 100,000 orders per sync; a cursor that never ends must not hang the sync


class SquareClient:
    def __init__(self, token: Optional[str] = None, env: Optional[str] = None, location_id: Optional[str] = None,
                 get=fetch.get_json):
        self.token = token or os.environ.get("SQUARE_ACCESS_TOKEN")
        if not self.token:
            raise fetch.FetchError("Square isn't connected: set SQUARE_ACCESS_TOKEN (see .env.example).")
        self.env = env or os.environ.get("SQUARE_ENV", "sandbox")
        if self.env not in HOSTS:
            raise fetch.FetchError(f"SQUARE_ENV must be sandbox or production, not {self.env!r}")
        self.location_id = location_id or os.environ.get("SQUARE_LOCATION_ID")
        self._get = get  # tests swap in a fake

    @property
    def synthetic(self) -> bool:
        return self.env == "sandbox"

    def _call(self, path: str, body: Optional[dict] = None) -> dict:
        return self._get(HOSTS[self.env] + path, {}, "Square", body=body,
                         headers={"Authorization": f"Bearer {self.token}", "Square-Version": VERSION})

    def locations(self) -> List[str]:
        if self.location_id:
            return [self.location_id]
        ids = [loc["id"] for loc in self._call("/v2/locations").get("locations", []) if loc.get("status", "ACTIVE") == "ACTIVE"]
        if not ids:
            raise fetch.FetchError("Square has no active locations for this account")
        return ids

    def iter_orders(self, since: datetime, until: datetime) -> Iterator[dict]:
        """Raw completed Square orders closed in [since, until), oldest first, across all pages."""
        cursor, seen, locations = None, set(), self.locations()
        for _ in range(MAX_PAGES):
            body = {"location_ids": locations, "limit": PAGE,
                    "query": {"filter": {"state_filter": {"states": ["COMPLETED"]},
                                         "date_time_filter": {"closed_at": {"start_at": since.isoformat(), "end_at": until.isoformat()}}},
                              "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"}}}
            if cursor:
                body["cursor"] = cursor
            page = self._call("/v2/orders/search", body)
            yield from page.get("orders", [])
            cursor = page.get("cursor")
            if not cursor:
                return
            if cursor in seen:
                raise fetch.FetchError("Square sent the same cursor twice; stopped so the sync can't loop forever")
            seen.add(cursor)
        raise fetch.FetchError(f"Square returned more than {MAX_PAGES} pages; sync a shorter window")


def _gbp(money: Optional[dict]) -> float:
    if not money:
        return 0.0
    if money.get("currency", "GBP") != "GBP":
        raise ValueError(f"currency {money.get('currency')}: only GBP is supported")
    return money.get("amount", 0) / 100  # Square amounts are in pence


def to_pos_order(o: dict) -> dict:
    """One Square order → the POS input format (schema.py, POS section). Raises ValueError if it can't be mapped."""
    items = []
    for li in o.get("line_items") or []:
        qty = float(li.get("quantity") or 0)
        mods = [{"name": m.get("name") or "", "value": "", "price_gbp": _gbp(m.get("base_price_money")) * float(m.get("quantity") or 1)}
                for m in li.get("modifiers") or []]
        unit = _gbp(li.get("base_price_money"))
        gross = _gbp(li["gross_sales_money"]) if li.get("gross_sales_money") else round(qty * (unit + sum(m["price_gbp"] for m in mods)), 2)
        items.append({"sku": li.get("catalog_object_id") or f"SQ:{(li.get('name') or 'item').strip().lower()}",
                      "name": li.get("name") or "Item", "variant": li.get("variation_name") or None, "quantity": qty,
                      "unit_price_gbp": unit, "modifiers": mods, "line_total_gbp": gross})
    subtotal = round(sum(i["line_total_gbp"] for i in items), 2)
    discount = _gbp(o.get("total_discount_money"))
    tenders = sorted({(t.get("type") or "other").lower() for t in o.get("tenders") or []})
    return {"order_id": f"SQ-{o['id']}", "placed_at": o.get("closed_at") or o.get("created_at"),
            "location_id": o.get("location_id") or "square", "source": "square", "fulfilment": None, "items": items,
            # net sales: before tax added on top and tips (Square's total_money includes those)
            "subtotal_gbp": subtotal, "discount_gbp": discount, "total_gbp": round(subtotal - discount, 2),
            "payment": {"method": "+".join(tenders) or None, "status": "paid"}, "order_status": "completed"}


def sync(client: Optional[SquareClient] = None, days: int = 7, now: Optional[datetime] = None, dry_run: bool = False):
    """Record the last `days` of Square orders through backend.sales. Returns (SyncResult or None, mapping errors)."""
    client = client or SquareClient()
    now = now or datetime.now(timezone.utc)
    orders, errors = [], []
    for o in client.iter_orders(now - timedelta(days=days), now):
        try:
            orders.append(to_pos_order(o))
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"{o.get('id', '?')}: {e}")
    if dry_run:
        return None, errors, orders
    from backend import sales
    result = sales.record_orders(orders, origin=f"Square ({client.env}, last {days} days)", now=now, synthetic=client.synthetic)
    result.errors = errors + result.errors
    return result, errors, orders


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Read completed Square orders (read-only) into CoFoundr.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result, errors, orders = sync(days=args.days, dry_run=args.dry_run)
    print(result.message if result else f"{len(orders)} order(s) read, £{sum(o['total_gbp'] for o in orders):,.2f} (dry run)")
    for e in errors[:5]:
        print("  ", e)


if __name__ == "__main__":
    main()
