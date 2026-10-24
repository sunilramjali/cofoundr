# Ceyda — Work Log

Newest entry at the top. Format:

```
## <Day> <date>, <time> — <short title>
- **Did:**
- **Status:** done / in progress / blocked
- **Next:**
- **Blockers / notes for partner:**
```

---

## Sat 24 Oct, 15:45 — POS connector: synthetic sales + stock as a till API (MVP2)
- **Did:** `backend/inputs/pos/`: synthetic GAIA exports in `data/` (Sep–Nov 2026: 3,969 POS orders + opening/closing stock counts per month) and `client.py` serving them like a real POS API: `list_sales(since, until, limit, cursor)`, `iter_sales()`, `list_stock()`, `list_stock_counts()`. Never returns anything after "now" (files run to 30 Nov; `PosClient(now=...)` pins the clock for demos); bare dates = London midnight; refuses files not labelled `synthetic_mock_data`. 11 tests in `tests/test_edges.py` (76 pass). PR #11 merged into `stage`
- **Status:** done (read side); nothing written to MongoDB yet
- **Next:** `pos/sync.py`: client → `sales` (time-series) + `products`, once the shapes are agreed
- **Blockers / notes for partner:** Sunil, this may cover MVP2's "synthetic POS sales history" (3 months, not 6); check before generating more. Please define `Sale`/`Product` in `schema.py`; the order format in `client.py` is a proposal
