# CoFoundr: MVP Stages

Each stage must be **demo-able end to end** before starting the next. If time runs out, the last finished stage is what we demo.

**Current stage: MVP1**

---

## MVP1: One invoice, chased with approval (end to end)

**Target:** Sat 24 Oct, 18:00

### Goal
One invoice goes all the way through CoFoundr **with no human step until the approval**: **invoice emailed in → CoFoundr picks it up from Gmail on its own → Gemini reads and checks it → stored in MongoDB → flagged overdue → reminder drafted → owner approves → status updated**, visible in the dashboard. Dashboard upload stays as a **manual fallback**. Saint can read a `/brief` from the bridge on Fly.io.

This is the smallest slice that proves the core story: *an agent that does a real job on its own and stops to ask before touching money.*

### Must have (done criteria)
- [x] **Data:** MongoDB Atlas cluster live; `businesses`, `invoices`, `approvals` collections seeded with synthetic GAIA Coffee data (incl. 2–3 overdue invoices), labelled synthetic
- [ ] **Contract:** `backend/db/schema.py` shapes for `Business`, `Invoice`, `Approval` agreed and used by all code
- [ ] **Autonomous intake (Gmail):** a dedicated demo Gmail inbox is checked automatically (about every minute, running on Fly.io). Each new email's invoice attachments (PDF/image) are extracted, verified and saved, then the overdue check runs, with no clicks. Each email is processed once (by Message-ID) and every step is logged in `agent_log`. A "Check inbox now" button exists for the live demo. Dashboard upload remains as the fallback.
- [x] **Extraction:** an invoice photo/PDF (from email or upload) → `gemini-4-argon` returns supplier, amount, due date, line items as JSON
- [x] **Verification loop:** line items sum to total and due date is valid; otherwise `extraction_confidence` is low and the invoice is flagged for owner review (not guessed)
- [x] **Overdue + draft:** overdue invoices get a drafted reminder saved as a `pending` approval
- [x] **Approval:** dashboard shows the queue with Approve / Edit / Reject; decision updates MongoDB (`status`, `decided_via`, `decided_at`)
- [x] **Web app on Fly.io:** (live: https://cofoundr.fly.dev) one FastAPI app serving the dashboard pages (upload + approval queue) and `GET /health`, `GET /brief?token=…` (plain text from MongoDB: overdue invoices + pending approvals)
- [ ] **Saint test:** Saint reads `/brief` from the Fly.io app and relays it on WhatsApp
- [ ] **WhatsApp automatic updates:** the owner is messaged on WhatsApp **without asking** when something needs them, e.g. "New invoice from Eastside Dairy (£176.70) read and checked" or "Reminder drafted for Hackney Hub, reply approve 4". CoFoundr publishes a `GET /updates?since=…` feed (token-protected; new invoices, new drafts, decisions); **Saint** polls it or receives a webhook and sends the message. Fallback if Saint can't send business-initiated messages: Twilio WhatsApp sandbox. Depends on the Saint team's answers.

### Out of scope for MVP1
Forecasting, prioritisation, events/weather, WhatsApp photo input, Atlas Search/Vector Search, Treg, heatmap, waste, partnerships, voice orders. (`/approve` via WhatsApp is already built.)

### MVP1 demo (≤ 60 s)
1. Email an invoice to the CoFoundr inbox → within a minute it appears in the dashboard, read and checked, with nobody touching it (agent activity shows each step). Fallback: upload it in the dashboard.
2. It's overdue → a drafted reminder appears in the approval queue.
3. Click Approve → status changes in MongoDB and the dashboard.
4. The owner's phone buzzes: Saint sends the WhatsApp update unprompted. Owner replies "approve 4" → dashboard shows it approved via WhatsApp.
5. Owner asks Saint for the brief → it lists what's left.

### Owners
| Task | Owner |
|---|---|
| Atlas cluster, collections, `schema.py`, synthetic seed data | Sunil |
| Gemini extraction + verification loop (needs Argon API key) | Sunil |
| Overdue check + reminder draft | Sunil |
| Dashboard pages on Fly.io: upload + approval queue | Sunil |
| `/brief` on Fly.io | Sunil |
| **Gmail autonomous intake** (demo Gmail account + `backend/inputs/gmail.py` → `pipeline.ingest_invoice`) | **Jade** (input) |
| **Saint test** (WhatsApp → `/brief`, `/approve`) | **Jade** (input/output) |
| **WhatsApp automatic updates**: `/updates` feed ✓ built (Sunil); Saint polling it / Twilio fallback | **Jade** (output) |
| **Upload retest + demo reset** (when Gemini's cap lifts) | **Sunil** (middle) |
| **Review PR #3** | **Jade** |

Items above the bold rows were built by Sunil solo. From now on: **Jade = inputs + outputs, Sunil = the middle** (see `CLAUDE.md` → Code layout & ownership).
**Open question for the Saint team:** can Saint message the owner unprompted, run on a schedule, or receive a webhook?

---

## MVP2: CoFoundr runs the owner's morning (DRAFT, review together)

**Target:** Sat 24 Oct, 23:00 · **Starts:** when MVP1 is ticked off

### Goal
Each morning the owner gets a WhatsApp brief **without asking**: what's coming in (a forecast with Gemini's reasons), what to do first (ranked priorities, one line of reasoning each) and what's waiting for a yes. Invoices can also come in as a WhatsApp photo, approved reminders are **really emailed**, and the data behind it all shows off more MongoDB Atlas features.

### Must have (done criteria)
**Inputs (Jade)**
- [ ] **WhatsApp invoice photos:** Saint forwards a photo to `POST /invoice` (token) → `pipeline.ingest_invoice(..., source="whatsapp_photo")` → Saint replies with `result.message`
- [ ] **Weather + events data:** Open-Meteo (no key) daily forecast for the truck's pitches + an events source (Ticketmaster or via Treg) stored in MongoDB (`events`), refreshed on a schedule

**Middle (Sunil)**
- [ ] **Sales history:** ~6 months of synthetic GAIA POS sales in a MongoDB **time-series** collection (`sales`)
- [ ] **Forecast:** baseline per day for the next 7 days from sales history, then **Gemini adjusts** for weather + nearby events and **explains why** ("+28% Sat: market nearby + sunny"); stored in `forecasts`
- [ ] **Prioritisation:** Gemini ranks open tasks (approvals, review flags, forecast prep like "order extra oat milk") by impact with a one-line reason each; stored in `tasks`
- [ ] **Atlas Search:** full-text search over invoices/suppliers (index + query function)
- [ ] **Vector Search / Automated Embedding:** semantic match of events to the business profile ("worth trading at?"), or similar past invoices; **Hybrid Search** if time allows
- [ ] **Error hardening:** Gemini 429s retried with backoff; every failure logged in `agent_log` and surfaced to the owner, never silent

**Outputs (Jade)**
- [ ] **Real reminder emails:** `outputs/email.py` sends approved reminders (Gmail SMTP from the demo inbox); `effect` shows "sent", never sends without approval
- [ ] **Morning brief pushed automatically** at ~08:00 via Saint: forecast + top 3 priorities + pending approvals (extends `brief.py`)
- [ ] **Dashboard:** forecast chart with Gemini's reasons, ranked priorities, invoice search box

### Out of scope for MVP2
Treg partnership finder, events heatmap, waste estimator, voice order entry (all MVP3).

### MVP2 demo (≤ 90 s, after the MVP1 demo)
1. Owner's phone: the 08:00 brief arrives unprompted with the forecast ("+28% Sat: market nearby + sunny") and top priorities.
2. Owner sends an invoice photo on WhatsApp → Saint replies "Added … checked ✓".
3. Owner replies "approve 2" → a real reminder email lands in the customer's (demo) inbox.
4. Dashboard: forecast chart with reasons, ranked priorities, search "dairy" finds the supplier's invoices (Atlas Search).

### Seams to agree before starting
- `POST /invoice` contract (Jade ↔ Sunil): multipart file + token → `IngestResult.message`
- `forecasts` and `tasks` document shapes in `schema.py` (Sunil defines, Jade's dashboard/brief reads)
- Weather/events shape in `events` (Jade writes, Sunil's forecast reads)

## MVP3: Polish + nice-to-haves (outline)
Events heatmap, waste estimator, partnership finder (Treg), voice order entry; demo video, README judging section. *To be detailed later.*
