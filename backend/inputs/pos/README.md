# backend/inputs/pos: POS sales + stock connector
**Owner: Jade**

GAIA Coffee's till, recreated as an API connection. All data here is **synthetic** and must stay labelled as such.

```
pos/
  data/        synthetic GAIA exports, Sep–Nov 2026: POS orders + opening/closing stock counts per month
  client.py    reads data/ as if it were a POS API: filter by time, page with a cursor, nothing after "now"
  sync.py      pulls from client.py and records it via backend.sales (a re-read window; duplicates skipped)
```

- `client.py` exposes the same calls a real POS (e.g. Square) would: `list_sales(since, until, limit, cursor)`, `iter_sales()`, `list_stock()` (latest count). It refuses files not labelled `synthetic_mock_data`. Swapping in a real POS later only changes this file.
- `sync.py` never writes MongoDB itself: it hands orders and counts to `sales.record_orders` / `sales.record_stock_count` (Sunil), which store `orders`, `sales` (time-series), `products` and `stock_counts`.
- Run it: `python -m backend.inputs.pos.sync` (last 7 days) · `--all` (backfill) · `--now 2026-11-30` (demo replay). From code: `sync(PosClient(), days=7)` → `PosSyncResult` (`.ok`, `.message`).
