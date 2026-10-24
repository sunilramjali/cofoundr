# CoFoundr: MVP Stages

Each stage must be **demo-able end to end** before starting the next. If time runs out, the last finished stage is what we demo.

**Current stage: MVP1**

---

## MVP1: One invoice, chased with approval (end to end)

**Target:** Sat 24 Oct, 18:00

### Goal
One invoice goes all the way through CoFoundr: **photo in → Gemini reads and checks it → stored in MongoDB → flagged overdue → reminder drafted → owner approves → status updated**, visible in the dashboard. Saint can read a `/brief` from the bridge on Fly.io.

This is the smallest slice that proves the core story: *an agent that does a real job and stops to ask before touching money.*

### Must have (done criteria)
- [ ] **Data:** MongoDB Atlas cluster live; `businesses`, `invoices`, `approvals` collections seeded with synthetic GAIA Coffee data (incl. 2–3 overdue invoices), labelled synthetic
- [ ] **Contract:** `backend/db/schema.py` shapes for `Business`, `Invoice`, `Approval` agreed and used by all code
- [ ] **Extraction:** upload an invoice photo/PDF → `gemini-4-argon` returns supplier, amount, due date, line items as JSON
- [ ] **Verification loop:** line items sum to total and due date is valid; otherwise `extraction_confidence` is low and the invoice is flagged for owner review (not guessed)
- [ ] **Overdue + draft:** overdue invoices get a drafted reminder saved as a `pending` approval
- [ ] **Approval:** dashboard shows the queue with Approve / Edit / Reject; decision updates MongoDB (`status`, `decided_via`, `decided_at`)
- [ ] **Web app on Fly.io:** one FastAPI app serving the dashboard pages (upload + approval queue) and `GET /health`, `GET /brief?token=…` (plain text from MongoDB: overdue invoices + pending approvals)
- [ ] **Saint test:** Saint reads `/brief` from the Fly.io app and relays it on WhatsApp

### Out of scope for MVP1
Forecasting, prioritisation, events/weather, WhatsApp photo input, `/approve` via WhatsApp, Atlas Search/Vector Search, Treg, heatmap, waste, partnerships, voice orders.

### MVP1 demo (≤ 60 s)
1. Upload an invoice photo in the dashboard → extracted fields appear (with confidence).
2. It's overdue → a drafted reminder appears in the approval queue.
3. Click Approve → status changes in MongoDB and the dashboard.
4. Show Saint reading `/brief` with the overdue invoice listed.

### Owners
| Task | Owner |
|---|---|
| Atlas cluster, collections, `schema.py`, synthetic seed data | Sunil |
| Gemini extraction + verification loop (needs Argon API key) | Sunil |
| Overdue check + reminder draft | Sunil |
| Dashboard pages on Fly.io: upload + approval queue | Sunil |
| `/brief` on Fly.io + Saint test | Sunil |

Sunil is solo for now; tasks can move to Jade (Ceyda) when she joins.

---

## MVP2: WhatsApp loop + forecast + priorities (outline)
Invoice photo and approvals via Saint/WhatsApp (`POST /invoice`, `POST /approve`), revenue forecast with Gemini reasoning, task prioritisation, morning brief, Atlas Search + Vector Search. *To be detailed once MVP1 is done.*

## MVP3: Polish + nice-to-haves (outline)
Events heatmap, waste estimator, partnership finder (Treg), voice order entry; demo video, README judging section. *To be detailed later.*
