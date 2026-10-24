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
| **Gmail autonomous intake** (incl. setting up the demo Gmail account) | **Jade** |
| **Saint test** (WhatsApp → `/brief`, `/approve`) | **Jade** (proposed) |
| **WhatsApp automatic updates**: `/updates` feed (Sunil) + Saint/Twilio sending | **Sunil** (proposed) |
| **Upload retest + demo reset** | **Sunil** (proposed) |
| **Review PR #3, PR #6** | **Jade** |

Items above the bold rows were built by Sunil solo. Bold rows: Jade owns Gmail intake (confirmed); the rest is proposed, confirm or swap.
**Open question for the Saint team:** can Saint message the owner unprompted, run on a schedule, or receive a webhook?

---

## MVP2: WhatsApp loop + forecast + priorities (outline)
Invoice photo and approvals via Saint/WhatsApp (`POST /invoice`, `POST /approve`), revenue forecast with Gemini reasoning, task prioritisation, morning brief, Atlas Search + Vector Search. *To be detailed once MVP1 is done.*

## MVP3: Polish + nice-to-haves (outline)
Events heatmap, waste estimator, partnership finder (Treg), voice order entry; demo video, README judging section. *To be detailed later.*
