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
