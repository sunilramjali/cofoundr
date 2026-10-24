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

## Sat 24 Oct, 16:05 — Till sync: POS → MongoDB via backend.sales (MVP2)
- **Did:** `backend/inputs/pos/sync.py`: `sync(client, days=7)` reads the connector and hands everything to `sales.record_orders` / `sales.record_stock_count` (never writes MongoDB itself). Re-reads a 7-day window each run (duplicates skipped, missed runs lose nothing); passes the till clock as `now`; reads the till fully first, so a broken file records nothing and is reported; bad stock counts reported, the rest kept. CLI: `python -m backend.inputs.pos.sync [--days N | --all] [--now DATE]`. 6 tests in `tests/test_edges.py` (108 pass). PR #14 merged into `stage`
- **Status:** done; not run against Atlas from my side (Sunil already loaded the data up to today)
- **Next:** weather (Open-Meteo) + events inputs for the forecast's Gemini adjustment; schedule the till sync (daily job or a "Sync till" dashboard button)
- **Blockers / notes for partner:** my `.env` keys are still empty; need my own Atlas user + keys from Sunil

## Sat 24 Oct, 15:45 — POS connector: synthetic sales + stock as a till API (MVP2)
- **Did:** `backend/inputs/pos/`: synthetic GAIA exports in `data/` (Sep–Nov 2026: 3,969 POS orders + opening/closing stock counts per month) and `client.py` serving them like a real POS API: `list_sales(since, until, limit, cursor)`, `iter_sales()`, `list_stock()`, `list_stock_counts()`. Never returns anything after "now" (files run to 30 Nov; `PosClient(now=...)` pins the clock for demos); bare dates = London midnight; refuses files not labelled `synthetic_mock_data`. 11 tests in `tests/test_edges.py` (76 pass). PR #11 merged into `stage`
- **Status:** done (read side); nothing written to MongoDB yet
- **Next:** `pos/sync.py`: client → `sales` (time-series) + `products`, once the shapes are agreed
- **Blockers / notes for partner:** Sunil, this may cover MVP2's "synthetic POS sales history" (3 months, not 6); check before generating more. Please define `Sale`/`Product` in `schema.py`; the order format in `client.py` is a proposal
