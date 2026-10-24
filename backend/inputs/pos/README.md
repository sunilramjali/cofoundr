# backend/inputs/pos: POS sales + stock connector
**Owner: Jade**

GAIA Coffee's till, recreated as an API connection. All data here is **synthetic** and must stay labelled as such.

```
pos/
  data/        synthetic GAIA exports, Sep–Nov 2026: POS orders + opening/closing stock counts per month
  client.py    reads data/ as if it were a POS API: filter by time, page with a cursor, nothing after "now"
  sync.py      (next) pulls from client.py and writes to MongoDB via schema.py names
```

- `client.py` exposes the same calls a real POS (e.g. Square) would: `list_sales(since, until, limit, cursor)`, `iter_sales()`, `list_stock()` (latest count). It refuses files not labelled `synthetic_mock_data`. Swapping in a real POS later only changes this file.
- Sales go to `schema.SALES` (time-series), stock to `schema.PRODUCTS`. The `Sale`/`Product` shapes are Sunil's to define in `schema.py`: agree them before `sync.py` writes anything.
