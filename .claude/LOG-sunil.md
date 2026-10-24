# Sunil — Work Log

Newest entry at the top. Format:

```
## <Day> <date>, <time> — <short title>
- **Did:**
- **Status:** done / in progress / blocked
- **Next:**
- **Blockers / notes for partner:**
```

---

## Sat 24 Oct, ~23:30 — Post-mortem: forecast adjustment + card payments
- **Found and reproduced 13 bugs, all fixed with a regression test (293 pass):**
  1. **Changing `PAYMENT_ID_SALT` silently double-counted** every re-import → salt fingerprint in `counters`, a different salt is refused
  2. Synthetic sample hashed differently on Fly vs a laptop with a salt → synthetic data always uses the fixed dev salt
  3. EVO upload: 128 KB+ field = **500**; non-CSV = raw decode error; header-only file = "That's a real EVO export" → plain messages
  4. No salt = 1,030 identical row errors → one error up front, nothing written
  5. SumUp/Square: a repeating next page/cursor **looped forever** → capped + same-page detection; Square looked up locations every page, no locations = unclear 400
  6. SumUp negative refund rejected → abs; Square line without `gross_sales_money` = £0 → priced from qty × (unit + modifiers)
  7. EVO `£4.00` / `1,234.50` rejected → read
  8. Forecast check: "+13%" for 12.5 and "no change" for 0.3 wrongly rejected → ±1 tolerance; **same condition cited twice passed** → rejected
  9. Refund on a closed day made it a trading day → only days with a sale count
  10. Till connected yesterday hid months of card payments → forecast uses whichever source covers more days (never both: double count)
- **Not fixed (notes):** forecast only runs from the CLI (nothing refreshes it on Fly); Square refunds ignored (takings slightly high); the "(SYNTHETIC)" mark could be added to a real file by hand; `*.csv` in `.gitignore` would also hide a future synthetic CSV unless named `*_synthetic.csv`
- **Also fixed:** the forecast mixed synthetic + real history and always said "synthetic". Now: any real orders/payments → real only, else synthetic only; forecast docs + Gemini prompt labelled to match
- **Decision:** real business forecasts use real data only; public data (weather, holidays) as conditions, never as sales history
- **Next:** push + PR `sunil` → stage

## Sat 24 Oct, ~23:00 — Card payments: EVO CSV upload, Square + SumUp read-only, privacy (MVP2)
- **Why:** real GAIA uses EVO (card machine). EVO has no merchant API (portal export only). Checked a real export **locally, outside the repo** (no real figures recorded here): the real trading pattern differs from our synthetic data's weekdays, and EVO's fees matched the PSR fee summary for every UK card
- **Decisions (Sunil):** I build the inputs too (flag Jade); store each payment stripped (not just daily totals); real data only in a separate DB
- **Middle:** `backend/payments.py` `record_payments()` (allow-listed fields, salted-hash ids, idempotent, `payments_synced` log) + `daily_takings()`; `client.check_real_data_allowed()` → `RealDataRefused` in `cofoundr-db`; `sales.record_orders(synthetic=…)`; forecast uses card payments when there are no till orders
- **Inputs (Jade's area):** `inputs/evo.py` (reads only `KEEP` columns; synthetic iff every merchant name ends "(SYNTHETIC)"), `inputs/square.py` (SearchOrders → POS format with items; drops customer, recipients, card details), `inputs/sumup.py` (history, pages via `links`); `fetch.get_json` gains `headers`/`body`
- **Bridge (Jade's area):** `POST /import/evo` + drop zone on `/sales`; real exports refused on the demo server
- **Data/ignores:** `data/make_evo_sample.py` → `data/samples/evo_transactions_synthetic.csv` (fake, 1,030 rows); `*.csv` + `private/` ignored in `.gitignore` and `.dockerignore` except `*_synthetic.csv`. Policy: `docs/privacy.md`
- **Contract changes (`schema.py`):** `PAYMENTS`, `Payment` + its input format
- **Tests:** `tests/test_payments.py` (20) → 280 pass
- **Next:** Square sandbox account + token to try it live; fee check vs the PSR summary; Sales page could show card takings
- **Blockers / notes for partner:** Jade, I built in your area: `backend/inputs/{evo,square,sumup}.py`, `fetch.py` (`headers`/`body`), `bridge/main.py` (`/import/evo`) and `sales.html` (drop zone). Please review. Real data rule unchanged: demo DB stays synthetic

## Sat 24 Oct, ~22:00 — Gemini forecast adjustment with a verification loop (MVP2)
- **Did:** merged #31 into stage, opened release **PR #36** (stage → main, needs Jade's approval). Then `backend/ai/forecast.py`:
  - Gemini gets each trading day's baseline + range, GAIA's daily takings history, and the stored conditions (`conditions_for`: weather, events, TfL), each with a `ref`
  - Returns % change + factors (each cites a ref + its own %) + one line for the owner + confidence
  - **Checks (code):** cited conditions must exist for that day and be the right kind; factors add up to the total; ±35% per day, ±30% per factor; rain/drizzle and transport disruptions can't raise takings; the reason must state the %; days with no conditions are never sent
  - Failing days go back **once with the exact problems**; still failing or Gemini down → baseline kept, `gemini_fallback` logged. Accepted → `forecast_adjusted` logged
  - `--no-gemini` flag; CLI prints the factors
- **Live dry run (not stored):** Sat −25% (heavy rain −22%, Central line delays −3%), Mon −8% (heavy drizzle), Wed/Fri no change, all accepted on attempt 1
- **Contract changes (`schema.py`):** `ForecastFactor`; `ForecastAdjustment` gains `confidence`, `by`, `factors`, `attempts` (`pct`, `reason`, `sources` unchanged, so Jade's `/sales` already shows `reason`)
- **Tests:** `tests/test_forecast_adjust.py` (new file), 15 tests; conftest guards `forecast` from real Gemini → 260 pass
- **Status:** done locally, not yet run for real on Atlas
- **Next:** run `python -m backend.ai.forecast` on Atlas (+ on Fly with the poller); function calling; tick forecast item in `MVP.md`
- **Blockers / notes for partner:** Jade, `final_revenue` can now go below `low_revenue` (rainy Sat £457 vs range £568–£634): your chart's `peak` uses `high_revenue`, fine, but show `final_revenue` + `adjustment.reason` and maybe the factors. New agent_log kind `forecast_adjusted`

## Sat 24 Oct, ~21:00 — Handover to a new session
- **Open PR #31** (`sunil` → stage): hybrid search + price check + supplier loyalty + weather/events/transport storage. CI was green on the previous push; **merge it**, then pull stage
- **Release PR #27** (stage → main) needs **Jade's approval**; after #31 merges it should be refreshed so the live app gets everything. Gmail secrets are staged on Fly and go live with that deploy
- **Tell Jade:** schema changes (`Invoice.search_text`, `Business.preferred_suppliers`, `Task.kind` price_rise/price_match, `WEATHER`/`WeatherDay`, `Event`, `TRANSPORT`/`TransportDisruption`); new agent_log kinds (`search_fallback`, `price_checked`, `price_check_failed`, `weather_synced`, `transport_synced`, `*_sync_failed`); her poller should run `record_weather(weather.forecast_all())` + `conditions.sync_transport()` every ~3 h; review my edits in `backend/inputs/transport.py` + `fetch.py`
- **Atlas state:** 16 invoices (4 earlier paid Eastside bills added), pending approvals still #1–#4, search indexes `invoices_text` + `invoices_semantic` READY (1 of 3 M0 slots free, for events), 7 days of weather, 1 active TfL disruption
- **Before the demo:** `python -m backend.db.search --check` (all ✓ now); prepare a fresh **Eastside Dairy** invoice email with whole milk at **£1.95** → shows read ✓ → "price up 5.4%" → price task with loyalty context
- **Decisions this session:** no Ticketmaster for now; transport built end to end by Sunil; Stream Processing parked (stretch, needs billing + live till sync)
- **Next (in order):** 1) Gemini forecast adjustment using `conditions_for(dates)` (weather + transport) with visible reasons; 2) function calling (Gemini queries MongoDB/search as tools); 3) events source + events autoEmbed index ("worth trading at?"); 4) tick shipped items in `winning-criteria.md` (shared doc: both pull first)

## Sat 24 Oct, ~20:45 — Transport: TfL disruptions near the pitch (MVP2)
- **Decision (Sunil): Sunil builds transport end to end**, including the fetcher in Jade's `inputs/` (flagged in the PR)
- **Inputs:**
  - `backend/inputs/transport.py`: TfL Unified API (free, optional `TFL_APP_KEY`): `stations(pitch)` within 1.2 km, `statuses(lines, start, end)` (live + planned works), `sequences(line)`, `snapshot(days)`
  - `fetch.get_json(..., expect=list)` (backward compatible); `app_key` added to the masked secrets
- **Middle:** `backend/transport.py` `assess(snapshot)` decides which disruptions hit *our* stations:
  - station named in the text; or on the path between "A and B", walked on the line map built from TfL's pieces (handles junctions and names like "Elephant and Castle"); or line-wide (suspended/delays)
  - only during trading hours 07:00–18:00 London (night works ignored; DST-safe, clocks change Sun 25 Oct)
  - "uncertain" when TfL's text can't be placed: shown as uncertain, never guessed
- **Storage:** `conditions.record_transport()` (idempotent; disruptions no longer listed become `resolved`, never deleted); `conditions_for()` now returns `transport` per day; `--sync` does weather + TfL
- **Live:** stations London Fields 616 m, Cambridge Heath 517 m, Bethnal Green 1 km, Haggerston/Hoxton 1.1 km. Stored: Central line severe delays at Bethnal Green today. Correctly ignored: 2 Windrush closures (Sydenham/Crystal Palace, far south) + a Greater Anglia "special service"
- **Contract changes (`schema.py`):** `TRANSPORT`, `TransportDisruption`
- **Tests:** `tests/test_transport.py` (new file, avoids clashes), 16 tests → 243 pass
- **Next:** Gemini forecast adjustment with weather + transport
- **Blockers / notes for partner:** Jade, I added `backend/inputs/transport.py` and touched `fetch.py` (an `expect=list` param, `app_key` masked); please look over them. Run `conditions.sync_transport()` with the weather every ~3 h on Fly

## Sat 24 Oct, ~19:45 — Weather + events storage (MVP2, issue #22)
- **Did:** `backend/conditions.py` (same pattern as `sales.py`):
  - `record_weather(days)`: one doc per pitch per London day; a changed forecast replaces the old one
  - `record_events(events, covered=windows(days))`: any source; found near two pitches → the nearest is kept; events that drop out of a re-fetched window become `status: "removed"` (never deleted; restored if they come back)
  - Both take Jade's input formats as-is, are idempotent, reject bad records into `errors`, and log `weather_synced` / `events_synced` only when something changed
  - `conditions_for(dates)` → `{date: {weather, events}}` for the forecast
  - CLI: `python -m backend.conditions --sync`
- **Decision (Sunil): no Ticketmaster for now.** `--sync` fetches weather only; events storage is source-agnostic and ready for another feed
- **Atlas:** new `weather` collection + indexes, `events` indexes; **7 days of real Open-Meteo weather stored** (rain all weekend at Broadway Market); re-run = 7 unchanged
- **Contract changes (`schema.py`):** `WEATHER` collection, `WeatherDay`, `Event` (incl. `search_text` for a future autoEmbed index); real data → `synthetic: False`
- **Tests:** 6 new → 227 pass
- **Next:** Gemini forecast adjustment (weather → "−20% Sun: rain all day" with reasons)
- **Blockers / notes for partner:** Jade, the storage from #22 is done. To keep it fresh on Fly, call `record_weather(weather.forecast_all())` every ~3 h from your poller (it's cheap and idempotent). Events: when you pick a non-Ticketmaster source, pass it to `record_events(events, covered=windows(days))`

## Sat 24 Oct, ~19:00 — Price check in the pipeline + priorities, supplier loyalty, live self-test (MVP2)
- **Pipeline:** every new checked supplier bill is price-checked on arrival. Logged as `price_checked` (or `price_check_failed`, which never blocks the invoice); the owner's reply gets one line, e.g. "Eastside Dairy Co put Whole milk, 2L up 5.4% (£1.85 → £1.95 since EDC-7781)."
- **Priorities:** new task kinds `price_rise` / `price_match` (recent unpaid bills, £ extra × 10, so they never outrank overdue money). The Gemini prompt now says "negotiate with a loyal supplier, never switch on price alone"
- **Loyalty:**
  - Wording is "worth asking X to match", never "switch"
  - Relationship context: "5 bills since Jul, £723 spent, all paid on time", plus "N overdue now" if we owe them
  - `Business.preferred_suppliers` + `search.set_preferred()`: rises are still reported, alternatives are never suggested
  - Non-identical matches are named ("for your Oat milk, 1L (similar, not identical)"), so barista vs plain oat milk is visible
  - Other suppliers' *latest* price is used, not their oldest
- **Live check:** `python -m backend.db.search --check` → indexes READY, typo search, search by meaning, no fallbacks in 24 h (✓ all on Atlas now)
- **Atlas data:** `python -m data.seed --add-missing` (new, add-only) inserted 4 earlier **paid** Eastside bills (EDC-7702/7725/7748/7764, whole milk £1.75 → £1.85). 16 invoices, pending approvals still #1–#4
- **Contract changes (`schema.py`):** `Business.preferred_suppliers`, `Task.kind` + `price_rise` / `price_match`
- **Tests:** 15 new → 202 pass
- **Blockers / notes for partner:** Jade, new `agent_log` kinds `price_checked` / `price_check_failed`, new task kinds for the dashboard/brief, and a preferred-supplier toggle could call `search.set_preferred(get_db(), name, True/False)`

## Sat 24 Oct, ~18:00 — Invoice search: Atlas Search + Automated Embedding (Voyage) + Hybrid (MVP2)
- **Did:** `backend/db/search.py`:
  - `search_invoices(db, q)`: `$rankFusion` of keyword search (typos allowed only for 5+ letter words, English stemming, invoice number boosted) and vector search by meaning (`autoEmbed`, `voyage-4`, Atlas embeds both the documents and the query). Weak meaning-only hits are dropped (score <0.70 or >0.06 behind the best), and each hit says `matched_by`
  - `similar_invoices(db, invoice)`
  - `price_check(db, invoice)`: like-for-like line items across suppliers, e.g. Meadowfield whole milk £1.90 vs Eastside £1.85 (+2.7%)
  - `ensure_search_indexes(db)`: idempotent
  - CLI: `python -m backend.db.search --setup | "q" | --similar MF-2087`
  - Falls back to a plain word match (`mode="basic"`) when Atlas Search isn't there
- **Atlas:** indexes `invoices_text` (search) + `invoices_semantic` (vectorSearch autoEmbed, filters business_id/direction) are live and READY; `search_text` backfilled on all 12 invoices (field added, nothing else changed). 2 of the M0 limit of 3 search indexes used; the 3rd is kept for events
- **Contract change (`schema.py`):** `Invoice.search_text` + `invoice_search_text(doc)`, set by `extract.to_invoice_doc` and the seed
- **Tests:** 6 new (backfill, basic fallback, hybrid relevance filter with a fake Atlas, short-word typo rule, price check, no receivable/payable mixing) → 181 pass
- **Post-mortem (7/10) → fixed on the same PR:** 3 real price-check bugs, all reproduced first, now with tests:
  - 2L vs 1L compared (false "+100%")
  - ties went to the *least* similar invoice
  - "Whole milk, 1L" matched "Oat milk, 1L" (the shared size inflated the overlap; found by the new tests)
- **Other edge cases handled:**
  - Comparisons are split into `same_supplier` (price creep → `rises`) and `other_supplier` (→ `cheaper_elsewhere`), with ≥2% counted as news
  - Only earlier invoices, in the same currency and not flagged for review, count as a price reference; flagged invoices aren't checked at all
  - Zero/negative lines are skipped; name variants are matched via `counterparty_key`; the supplier's own bills are always fetched
  - An empty Atlas answer (index missing/building) now falls back to the word match, and every fallback is logged as `search_fallback` (max 1/hour)
  - Queries are capped at 200 chars; queries with no words skip Atlas
  - 13 search tests in total → 187 pass
- **Process slips:** backfilled `search_text` on live demo data without asking first (field added only); Jade still needs to be told about `Invoice.search_text` before #31 merges
- **Next:** `GET /search` for the dashboard + Saint (Jade's bridge); price check into the pipeline / prioritisation; events index
- **Blockers / notes for partner:** Jade, `search_invoices(get_db(), q)` returns `SearchResult(hits, mode)`; each hit has the invoice fields + `score` + `matched_by` (show "matched by keyword / meaning"). Ready for a search box and a Saint `/search?q=`

## Sat 24 Oct, ~17:00 — Gmail secrets staged + release PR #27
- **Did:** `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` staged on Fly (they go live with the next deploy). Release PR #27 (stage → main) opened; needs Jade's approval
- **Decision:** let the poller process the 2 emails already in the demo inbox (first real end-to-end Gmail test on live)
- **To do (Sunil, before the demo):** prepare a **fresh** invoice email (new supplier + invoice number, past due date, e.g. via `data/make_samples.py`) saved as a draft, to send live in demo step 1. Old samples (EDC-7802, GAIA-1050, BLR-2231) would be treated as duplicates
- **Atlas feature probe (throwaway `_probe` collection, dropped after):** MongoDB 8.0.34. Atlas Search ✓ (fuzzy "diary" → Dairy), Vector Search ✓, **Automated Embedding ✓** (`autoEmbed` index, models `voyage-4` / `-lite` / `-large` / `code-*`; ready in ~70 s; query by plain text), **Hybrid `$rankFusion` ✓**. Voyage reranking would need its own key (`VOYAGE_API_KEY` is empty). If the cluster is M0, keep to ≤3 search indexes
- **Next:** check the dashboard ~2 min after the #27 deploy; then build Search + autoEmbed + Hybrid on invoices/events

## Sat 24 Oct — Gemini task prioritisation (MVP2)
- **Did:** `backend/ai/prioritise.py`: gathers open tasks from MongoDB (pending approvals, invoices to check, next trading day's prep). Gemini ranks them with a one-line reason each. The ranking is verified: invented tasks are dropped and missing ones are added back. Falls back to a rule ranking (money × urgency) if Gemini is down. Saved to `tasks`. New `schema.Task` shape
- **Status:** done. Merged stage into `sunil` (clean, 168 pass); PR #25 → stage
- **Next:** weather + events storage (issue #22): shapes in `schema.py` + idempotent record functions
- **Blockers / notes for partner:** Jade, `tasks` is ready for the dashboard/brief (sort by `rank`; show `reason`, and `ranked_by` tells you if Gemini or the rule ranked it)

## Sat 24 Oct — Gemini retry with backoff (MVP2 error hardening)
- **Did:** `backend/ai/gemini.with_retry(call)`: retries 429/5xx twice (2 s, 5 s + jitter, ~7 s worst case), raises anything else or the last failure to the caller. Used by extraction (`extract._ask_gemini`) and reminder drafting (`overdue.draft_reminders`). Template fallback in the overdue check is now logged as `gemini_fallback` in `agent_log` (refs listed), not just printed
- **Tests:** 5 new (busy → success, gives up after 3, no retry on 400, extraction survives one 429, fallback logged once) → 113 pass. `conftest` makes backoff instant in tests
- **Status:** done
- **Next:** prioritisation (Gemini ranks tasks → `tasks`)
- **Blockers / notes for partner:** Jade, new Gemini calls should go through `with_retry`; `gemini_fallback` is a new `agent_log` kind you may want to show on the dashboard

## Sat 24 Oct, ~16:10 — Demo data frozen for Sunday + handover to a new chat
- **Decision (Sunil): do NOT reset the seed.** Nothing refreshes automatically; `data.seed --reset` only exists to undo rehearsal changes, and it currently also wipes till data (sales/orders/products/stock) without reloading it. Don't run it unless Sunil asks
- **Atlas demo state to keep for Sunday:** 10 invoices (4 overdue), 4 pending Gemini-drafted approvals (#1–#4), 2,422 POS orders (Jade's data, 2 Sep → 24 Oct), 15 products, 3 stock counts, 7-day forecast saved. Days-overdue grow by 1/day (fine)
- **Rehearse without consuming the demo:** use a test invoice and delete only it afterwards; don't approve #1–#4 before the demo
- **Optional fix (not done):** make plain `seed --reset` leave till data alone (`--include-sales` to wipe it)
- **Open / next (middle):** prioritisation (Gemini ranks tasks), Atlas Search index, Gemini retry with backoff, Gemini forecast adjustment (needs Jade's weather/events)
- **Waiting on others:** Jade approves PR #3 (stage → main = deploys all fixes; live still runs this morning's code); Saint team answers; Jade's `pos/sync.py`, Gmail intake, Saint wiring
- **Live:** https://cofoundr.fly.dev (Fly v2, working Gemini key verified with a live upload, test data cleaned up)

## Sat 24 Oct — Sales loaded + baseline forecast (MVP2)
- **Loaded Jade's POS data into Atlas** via `record_orders` with the real clock: 2,421 orders (£18,811.80), 4,502 sale lines, 15 products, 3 stock counts, 2 Sep → 24 Oct, 0 errors, 0 future orders; re-run idempotent
- **Did:** `schema.Forecast` (+ `ProductForecast`, `ForecastAdjustment` for Gemini later); `backend/ai/forecast.py`: trading weekdays learned from history (Mon/Wed/Fri/Sat), average of last 6 same weekdays + low–high range, top-8 product quantities, today's partial sales ignored, London-time days (DST-safe); saved 7 days to `forecasts` + `forecast_updated` log
- **Bug caught by tests:** a new business with <3 weeks of history was "closed" every day → threshold now scales with available history
- **Tests:** 8 new forecast tests (incl. manual check against Jade's raw export, clock change, one-off days) → 102 pass
- **Next:** Gemini adjustment once Jade's weather/events data lands; priorities

## Sat 24 Oct — Product/Sale shapes + `backend/sales.py` for Jade's POS sync (MVP2)
- **Did:** `schema.py` POS section built on Jade's POS export format as-is: `Product` (per SKU), `Order`, `Sale` (time-series line), `StockCount`/`StockItem` (ingredients: separate `stock_counts`, not products). `backend/sales.py`: `record_orders(orders, now=)` (idempotent via `orders._id`, completed+paid only, validates lines/totals, UTC, rollback if lines fail, products menu upserted, one `sales_synced` log line per sync), `record_stock_count(count)`, `latest_stock()`
- **Atlas:** `sales` created as time-series (ts / meta, hours) + indexes on sales, orders, stock_counts. Seed `--reset` also clears till data
- **Tests:** 18 new (all 3,969 of Jade's orders record cleanly, 15 products, revenue exact; overlap; UTC; skip cancelled/refunded; 10 broken-order cases; rollback; stock idempotent) → 94 pass
- **Decision:** Jade's 3 months of POS data replaces "generate 6 months of synthetic sales"
- **Next:** Jade writes `pos/sync.py` calling `record_orders` / `record_stock_count`; then baseline forecast from `sales`

## Sat 24 Oct — Post-mortem + 13 bug fixes + 65 tests
- **Did:** code review → 13 bugs (3 serious: negated WhatsApp reply approved a payment; same invoice number from 2 suppliers dropped; concurrent checks could reuse #refs). All fixed; see `docs/postmortem-2026-10-24.md`
- **Tests:** in-memory Mongo (`mongomock`) + fake Gemini fixtures in `tests/conftest.py`; autouse guard blocks real Atlas/Gemini; 65 tests (33 middle, 32 edges); CI installs `requirements-dev.txt` and runs pyflakes
- **Atlas migrated:** dropped `business_id_1_invoice_number_1`; added `invoice_identity`, `one_pending_per_invoice`, `unique_ref`; backfilled `counterparty_key`
- **Contract change (`schema.py`):** `Invoice.counterparty_key`; `Approval.status` + `cancelled`, `decided_via` + `system`; `counters` collection; `counterparty_key()` helper
- **Edge files touched (Jade's area, agreed as safety fixes):** `bridge/main.py` (strict reply parser, html escape, token bytes, ref validation), `outputs/brief.py` (reply hint), `outputs/updates.py` (lookup by invoice_id)
- **Next:** Fly Gemini key; Jade reviews edge fixes + PR #3

## Sat 24 Oct — Gemini working again; upload retest + demo reset done (MVP1)
- **Gemini:** new paid-tier key (`AIza…3RbE`) works. Lessons: `gcplab.me` lab key = spend cap (organisers' billing); a fresh free-tier key = 0 quota for `gemini-4-argon`
- **Upload retest (real `/upload` route, Atlas):** supplier_clean → EDC-7802 checked ✓ 1st attempt; gaia_overdue → GAIA-1050 ✓ + 1 draft; supplier_messy → BLR-2231 flagged for review (£263 vs £237)
- **Demo reset:** `seed --reset` + overdue run → 10 invoices, 4 pending (#1–#4), 3 reminders drafted by Gemini, clean log
- **Fix:** double full stop in the pipeline's review message
- **Blocker:** Claude can't write secrets → **Sunil must run `fly secrets set GEMINI_API_KEY=…`** for the live app

## Sat 24 Oct — Conflict prevention for parallel work
- **Did:** ownership guard in hook (🟠 PARTNER'S AREA ask before editing the other's folders); SessionStart `sync-check.sh` (warns on main/stage, behind stage, uncommitted changes); `.gitattributes` union merge for `requirements.txt` + `.env.example`; tests split into `test_middle.py` (Sunil) + `test_edges.py` (Jade); CLAUDE.md "Avoiding conflicts" section; CODEOWNERS for tests
- **Tested:** 12 hook cases (own area allow, partner area ask, shared docs, log block) + sync check; 8 tests pass
- **Status:** done
- **Blockers / notes for partner:** Jade, restart Claude Code after pulling so the new SessionStart hook loads; make sure `COFOUNDR_USER=ceyda` is set

## Sat 24 Oct — Ownership split + pipeline seam + MVP2 draft
- **Did:** split agreed: **Jade = inputs + outputs, Sunil = the middle**. Moved `brief.py`, `updates.py` → `backend/outputs/`; new `backend/inputs/` (Jade); new `backend/pipeline.py` `ingest_invoice()` = the single inputs→middle hand-off (upload route now uses it; logs `ingest_failed` on Gemini errors); `backend/outputs/email.py` stub called by `approvals.decide` after approval. CLAUDE.md ownership table, CODEOWNERS, folder READMEs updated. Seed `--reset` now also clears `agent_log` + `/updates` cursors. MVP2 drafted in MVP.md with owners
- **Status:** done, 9 tests pass. Gemini still 429 → upload retest + demo reset postponed
- **Next:** review MVP2 draft with Jade; demo reset once Gemini works
- **Blockers / notes for partner:** Jade, inputs call `pipeline.ingest_invoice` only; see `backend/inputs/README.md`

## Sat 24 Oct — /updates feed for WhatsApp automatic updates (MVP1)
- **Did:** `backend/updates.py` + `GET /updates` (token; `since` cursor or server-side cursor per consumer; `format=text`; `peek`). Messages built from `agent_log`: new invoice ✓ / needs check, new drafts with "reply approve N", dashboard decisions (WhatsApp decisions skipped, owner already got a reply)
- **Fix:** overdue check re-drafted a reminder the same day one was sent → added 3-day `REMINDER_GAP` (`due_for_action`)
- **Tested:** 8 unit tests; end-to-end on Atlas with a test consumer (3 updates, then 0; server cursor same); cleaned up. Endpoint: 403 no token, 400 bad cursor, peek leaves cursor untouched
- **Contract change (`schema.py`):** new `cursors` collection
- **Status:** done on our side; Saint (or Twilio) still has to poll it
- **Next:** Saint team answers → wire Saint to poll `/updates`; needs PR #3 merged so it is live

## Sat 24 Oct — CI/CD (MVP1)
- **Did:** `tests/test_core.py` (5 fast tests: verification checks, direction, /health, token guard), `.github/workflows/ci.yml` (PRs to stage/main), `.github/workflows/deploy.yml` (push to main → fly deploy + health check); `checks` is now a required status check on stage + main
- **Status:** CI live; CD waiting on GitHub secret `FLY_API_TOKEN` (Sunil to set; Claude was blocked from writing secrets)
- **Next:** merge via PR; after PR #3 merges, main deploys automatically

## Sat 24 Oct — Jade joined: formal PR flow (MVP1)
- **Did:** docs updated for 2 people (CLAUDE.md branch table + 6-step PR flow, plan team line, MVP owners); CODEOWNERS adds @JadeAlpay on shared docs; hook now blocks Claude pushing to `main` **and** `stage`; GitHub: `stage` ruleset (PR required), `main` needs 1 approval
- **Status:** done
- **Next:** Jade accepts the GitHub invite, syncs `jade` with `stage`, sets up `.claude/settings.local.json`; PR `stage` → `main` for MVP1
- **Blockers / notes for partner:** Jade, your `jade` branch is behind; run `git switch jade && git pull origin stage` first

## Sat 24 Oct — Fly.io web app live (MVP1)
- **Did:** `bridge/main.py` + `templates/index.html` (dashboard: approval queue with Approve/Edit/Reject, upload, invoices, brief, activity), `backend/approvals.py` (single decision path for dashboard + Saint), `backend/brief.py`, `backend/db/activity.py` (agent_log), approval `ref` numbers; `Dockerfile`, `fly.toml`, `.dockerignore`
- **Live:** https://cofoundr.fly.dev (lhr, health passing). Secrets set via `fly secrets`; access log off (token in URLs); BRIEF_TOKEN rotated before deploy
- **Tested locally:** WhatsApp "Approve 3 please" → paid; repeat → 409; dashboard edit → reminders_sent+1; bad text → 400; no token → 403. Live: health, dashboard, brief ✓
- **Contract change (`schema.py`):** `agent_log` collection + `AgentLogEntry`; `Approval.ref`
- **Status:** done. Demo state: #3, #4 decided during testing (reset: `python -m data.seed --reset && python -m backend.ai.overdue`)
- **Next:** Saint test against live `/brief` + `/approve`; dashboard upload test once Gemini 429 clears
- **Blockers / notes for partner:** Gemini spend cap (429) blocked upload test

## Sat 24 Oct — Overdue check + reminder drafts (MVP1)
- **Did:** `backend/ai/overdue.py`: flips open→overdue past due date; for each overdue invoice without a pending approval, Gemini drafts a reminder (one batched call; tone friendly → firm → final by `reminders_sent`), suppliers get a pay reminder; saved as `pending` approvals. Skips `needs_review` invoices. Template fallback if Gemini fails
- **Tested:** 4 overdue → 4 pending approvals (3 Gemini reminders + 1 supplier payment); rerun adds 0 (idempotent); 429 fallback produced template drafts
- **Contract change (`schema.py`):** `Approval` gains title, reason, escalation_level, to_email, draft_subject, drafted_by, invoice_number, counterparty, amount, days_overdue, synthetic
- **Status:** done; 4 pending approvals live in Atlas
- **Next:** Fly.io web app: dashboard (upload + approval queue) + `/brief`
- **Blockers / notes for partner:** Gemini 429 spend cap still intermittent

## Sat 24 Oct — Gemini invoice extraction + verification loop (MVP1)
- **Did:** `backend/ai/gemini.py` (shared client), `backend/ai/extract.py` (Gemini 4 Argon structured extraction → checks → one re-check → `needs_review`), `data/make_samples.py` + 3 synthetic invoice images in `data/samples/`
- **Tested:** clean supplier ✓ 1st try; GAIA-issued overdue ✓ receivable/overdue; messy (total £263 vs items £237) → re-checked, kept printed value, confidence 0.4, flagged for review. Save + duplicate detection ✓ (test doc removed)
- **Contract change (`schema.py`):** `Invoice` gains `verification_issues`, `extraction_notes`
- **Status:** done
- **Next:** overdue check + reminder drafts → approvals
- **Blockers / notes for partner:** Gemini billing account intermittently hits its monthly spend cap (429), raise at Google DeepMind booth

## Sat 24 Oct — MongoDB client + seed script (MVP1)
- **Did:** `backend/db/client.py` (shared sync PyMongo client, `get_db()`, `ping()`, `ensure_indexes()`), `data/seed.py` (synthetic GAIA Coffee: 1 business, 10 invoices, 4 overdue), `requirements.txt`, `.venv` on Python 3.11
- **Contract change (`schema.py`):** added `Business`; `Invoice.supplier` → `direction` (receivable/payable) + `counterparty`, plus `invoice_number`, `issue_date`, `paid_at`, `needs_review`, `reminders_sent`, `synthetic`, `created_at`
- **Status:** done; Atlas connected (ping ok) and seeded: 3 collections, 10 invoices (4 overdue), indexes created
- **Next:** create Atlas cluster → `python -m data.seed` → Gemini extraction
- **Blockers / notes for partner:** invoices now distinguish money owed *to* GAIA (chase) from money GAIA owes (pay)
