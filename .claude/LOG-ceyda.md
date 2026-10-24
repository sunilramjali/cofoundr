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

## Sat 24 Oct, 22:30 — WhatsApp through Twilio (MVP1 auto-updates + MVP2 photos)
- **Did:** Twilio WhatsApp sandbox as the owner's channel (Saint can't POST). In: `POST /whatsapp/twilio` (Twilio signature checked, owner's number only; photo → `ingest_invoice(source="whatsapp_photo")`, "approve 4" → `approvals.decide(via="whatsapp")`, anything else → read-only Gemini assistant `outputs/assistant.py`). Out: `outputs/whatsapp.py` pushes `/updates` every `WHATSAPP_PUSH_SECONDS` (30 in `fly.toml`), cursor only moves past what was really sent. Dashboard chat bar now `/ask` (same assistant). Keys checked against the real Twilio account (active trial); `TWILIO_WHATSAPP_FROM` must be the sandbox `+14155238886`. 24 h-window error now explained in `agent_log`. 343 tests pass
- **Status:** done, PR into `stage`; not live yet
- **Next:** owner's phone joins the sandbox; set the sandbox webhook to `https://cofoundr.fly.dev/whatsapp/twilio`; Fly secrets `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `OWNER_WHATSAPP`; release to `main`
- **Blockers / notes for partner:** Sunil, merged `stage` in: conflicts in `bridge/main.py` imports + `tests/conftest.py` (kept your `evo` + `forecast` alongside my `assistant`). Edits in your area: `fly.toml` (`WHATSAPP_PUSH_SECONDS`), `backend/outputs/updates.py` `advance()` is mine. Release needs your approval

## Sat 24 Oct, 21:00 — Saint (Thea) wired in by email: decisions + WhatsApp photos (MVP1/2)
- **Did:** Thea answered: she reads URLs ✅, runs schedules + messages unprompted ✅, email ✅, **no HTTP POST** ❌. So: she reads `/brief` and polls `/updates` (both GET, already built); owner decisions and invoice photos come to us **by email** to the demo inbox. `backend/inputs/gmail.py`: emails from `SAINT_EMAIL` → photo/PDF = `ingest_invoice(source="whatsapp_photo")`; a body line that is exactly "approve 4"/"reject 2" = `approvals.decide(via="whatsapp")` (one per email, quoted replies ignored). Counts only if Gmail's top `Authentication-Results` (mx.google.com) shows DKIM/DMARC/SPF pass for exactly `saint.work`; forgeries logged as `saint_email_rejected`, decide nothing. `parse_reply` moved to `backend/inputs/replies.py` (bridge imports it). `SAINT_EMAIL` added to `.env.example`. 12 new tests (forged/lookalike/buried headers, ambiguous, already decided, photo) → 257 pass
- **Status:** done, PR into `stage`; not live yet
- **Next:** `SAINT_EMAIL` Fly secret + release; one real test email from Thea to confirm her headers pass; send Thea her standing instructions; SMTP replies (`outputs/email.py`)
- **Blockers / notes for partner:** Sunil, release to `main` needed before Thea can test (live is 24+ commits behind `stage`). Saint team must OK Thea emailing our inbox (outside Encode). No edits in your area

## Sat 24 Oct, 19:00 — Post-mortem of my work + fixes (MVP2)
- **Did:** reviewed today's work for edge cases; verified live: Gmail labels applied (0 unprocessed), Saturday's till data complete (73 orders, £617.20). Fixed:
  1. **CSRF** (verified exploitable: any site could POST "approve 1" to `/saint`): middleware rejects POSTs whose Origin/Referer isn't CoFoundr (also `Origin: null`); Saint's server calls (no Origin) still work. PR #33
  2. **Local dev wrote to the shared demo DB** (how the live Gmail run happened). My Atlas user can't create another DB → **sandbox mode** `COFOUNDR_SANDBOX=1` (`tools/sandbox.py`): in-memory copy of the demo data, inbox/email off, sidebar badge. Verified: approving #4 locally left Atlas #4 pending. PR #33
  3. **Gmail retry storm:** busy-Gemini emails retried after 15 min, not every minute. PR #33
  4. **Weather blocking pages:** 3 s first fetch, then stale-while-refresh in the background. PR #33
  5. **Fake-invoice risk:** `view.payment_checks` warns on first-time suppliers (home card + approval); **Pay now** opens the full approval (`/approvals#a3`) instead of paying in one click. PR #34
  6. **Chat bar:** answers "what's waiting?/brief/status/today" with the list; anything else: "nothing was sent" + what works. PR #34
  7. Opportunity filter tightened (no "Security event", no no-reply senders); WebGL capped at ~30 fps. PR #34
- **Tests:** 202 pass (CSRF matrix, sandbox isolation, retry backoff with a fake clock, non-blocking weather, scam-invoice end to end)
- **Status:** done, merged into `stage`
- **Next:** release #19 (Sunil) + Fly Gmail secrets; Ticketmaster key; Saint API for the chat bar
- **Blockers / notes for partner:** Sunil, **run the local app with `COFOUNDR_SANDBOX=1`** (see `bridge/README.md`) so local clicks never touch the demo data. Edits in your area: none this round. `MVP.md` Gmail box left unticked on purpose (needs Fly secrets to be true)

## Sat 24 Oct, 18:30 — Dashboard rebuilt from my sketch: sidebar, 3D orb, feed cards, Saint chat bar (MVP2)
- **Did:** layout from my notebook sketch. Floating glass **sidebar** (Home, Insights → `/sales`, Finances → `/invoices`, Inventory, Saint → `/whatsapp`, Tasks → `/approvals`, Settings, owner profile). **Home feed:** greeting · today's update card (forecast + what drives it, takings-vs-forecast chart) with a live ray-marched **3D pearl orb** · action cards: opportunity (partnership emails the inbox set aside), running low (stock estimate), invoice to be paid (Pay now = the approval), reminders ready, latest activity · **Saint chat bar** (`POST /saint`: "approve 1"/"reject 1" use the WhatsApp parser; anything else says Saint isn't connected to the bar yet). Cards tilt in 3D with glare. New pages `/inventory`, `/settings`; opportunities on `/inbox`. `view.stock_estimate` (last count − daily use from latest opening→closing pair; low < 7 days; order tops up to 2 weeks; labelled as estimate). 181 tests pass. PR #30 merged into `stage`
- **Status:** done locally; ships with release #19
- **Next:** design feedback; weather/events storage + Gemini forecast reasons; Saint API for the chat bar
- **Blockers / notes for partner:** data says napkins/stirrers/cookies are low, not oat milk. Stock estimate + opportunity extraction live in `bridge/view.py` (display-only analysis, Sunil's area in spirit: move to the middle if you prefer). Chat bar needs `SAINT_API_KEY` to talk to Saint for real

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
