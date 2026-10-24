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

## Sat 24 Oct, 18:00 — Dashboard redesign: modular home + glass (MVP2) · Gmail intake ran live
- **Did:** dashboard rebuilt in 3 rounds (feedback-driven): dark editorial (#24) → light **modular home** (#26) → **living backdrop + frosted glass** (#28). Now: home = 7 modules (Your yes, Sales & the week, Overdue to you, Inbox, What I did on my own, Invoices, On WhatsApp); each opens into its own page (`/approvals`, `/sales`, `/invoices[?show=overdue]`, `/inbox`, `/activity`, `/whatsapp`) with a module→page morph (cross-document View Transitions). Slow WebGL gradient backdrop (continuous across pages, reduced-motion + no-WebGL fallbacks), glass surfaces, Instrument Serif + Inter. New: `bridge/view.py` (read-only helpers incl. 28-day sales history, weather cached 30 min), `bridge/static/`, `templates/base|page|home|…`. Actions return to their page via a whitelisted `back` field (no open redirect). Pages server-rendered, work without JS (Saint can read them). Tests → 175 pass
- **Gmail intake ran live** (via "Check inbox now"): 2 email-body invoices read by Gemini, both checked ✓ on attempt 2 (Ember & Oak GR-2026-1048 £91.00, Meadowfield MF-2087 £54.10), saved to Atlas; 6 other emails labelled only; approvals #1–#4 untouched, none new
- **Status:** design merged into `stage`; local only until release #19 is approved
- **Next:** design feedback round; weather/events storage + Gemini forecast reasons; Saint wiring
- **Blockers / notes for partner:** Sunil, the live Gmail run answers the "OK a real Gmail run?" item in #22 (Atlas now has 12 invoices). Release #19 now also contains the redesign

## Sat 24 Oct, 17:30 — Gmail autonomous intake (MVP1)
- **Did:** `backend/inputs/gmail.py`: demo inbox over IMAP (app password). Reads PDF/image attachments, and invoices written in the email body when the subject mentions an invoice (the demo inbox's 2 invoices are body-only). Gmail label `CoFoundr/processed` = handled once; Gemini-busy → retried next check; nothing marked read. Background poll every `GMAIL_POLL_SECONDS` (60 in `fly.toml`) + **Check inbox now** button (`POST /check-inbox`). 13 tests (fake IMAP + fake Gemini) → 149 pass. Read-only preview on the real inbox: 2 invoices would be read, 6 other emails only labelled. PR #21 merged into `stage`
- **Edits in Sunil's area:** `backend/pipeline.py` (`text/plain` allowed), `tests/test_middle.py` (unsupported example → zip), `fly.toml` (`GMAIL_POLL_SECONDS`)
- **Status:** built; not run live yet (no real run against frozen Atlas; Fly secrets not set)
- **Next:** live run once OK'd; Fly secrets; ship in release #19
- **Blockers / notes for partner:** **Sunil, your to-do list is issue #22**: https://github.com/sunilramjali/cofoundr/issues/22 (approve #19, Fly Gmail secrets, Sunday release, OK the live Gmail run, weather/events storage, review my edits in your area)

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
