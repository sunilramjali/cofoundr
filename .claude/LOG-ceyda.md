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

## Sat 24 Oct, 16:35 — Releases + working solo
- **Did:** approved + merged release PR #3 (stage → main): deploy succeeded, live `/health` ok, live app now has MVP1 fixes, forecast, POS connector, till sync, weather/events inputs. Merged Sunil's PR #17 (Gemini retry with backoff) and my PR #18 (log + main's merge commit) into `stage`; `sunil`, `jade`, `main` all contained in `stage`. Opened release PR #19 (stage → main: #17 + log)
- **Status:** PR #19 waiting for Sunil's approval
- **Next:** merge #19 once approved, check deploy; continue on `jade`
- **Blockers / notes for partner:** Sunil is out of Claude tokens, so I'm working solo: staying on `jade`, every change goes `jade` → PR → `stage`, merged as soon as CI is green (no direct pushes; `stage` is protected and I'm not admin). Sunil, **releases to `main` still need your approval on the GitHub website** (I can't approve my own); please approve #19, and look out for one more release Sunday morning before 12:00

## Sat 24 Oct, 16:15 — Weather + events inputs for the forecast (MVP2)
- **Did:** `backend/inputs/`: `weather.py` (Open-Meteo daily forecast, no key, 7 days per pitch, London dates; verified live), `events.py` (Ticketmaster Discovery, events within 3 km over 7 days; venue distance, paging cap 5, dedupe, cancelled/malformed left out), `fetch.py` (stdlib HTTP helper; plain `FetchError`s, API keys never in messages), `pitches.py`. Both return plain dicts, never write MongoDB; formats in `backend/inputs/README.md`. CLIs: `python -m backend.inputs.weather` / `.events`. 24 tests (fake HTTP; autouse guard blocks real calls) → 132 pass. PR #16 merged into `stage`
- **Status:** inputs done; events not run live (no `TICKETMASTER_API_KEY` yet); nothing stored yet
- **Next:** weather/events sync once the middle's record functions exist; confirm pitches with the owner
- **Blockers / notes for partner:** Sunil, please add weather + event shapes to `schema.py` (weather in `events` or its own collection?) and idempotent record functions (weather by `pitch_id`+`date`, events by `source`+`source_id`). Pitch is an **assumption**: Broadway Market only. I still need a Ticketmaster key and my own Atlas user

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
