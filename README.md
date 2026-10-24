# CoFoundr

**An AI co-founder for small business owners who can't afford to hire a team.**

CoFoundr watches a small business's data, spots what needs doing, ranks it by impact and drafts the actions. The owner talks to it on WhatsApp and stays in control: **every money action needs the owner's approval first.**

> Built at Encode London Hackathon 2026 (23–25 Oct). Demo business: GAIA Coffee, an East London mobile coffee truck. **All business data in this repo is synthetic.**

---

## The problem

Small business owners do every job themselves: serving customers, ordering stock, chasing invoices, planning where to trade next week. The admin happens late at night, or not at all. Unpaid invoices slip, stock gets over-ordered and wasted, and busy days (a market nearby, a sunny Saturday) get missed because nobody had time to look.

A big company would hire an ops manager, a bookkeeper and an analyst. A one-truck coffee business can't.

## Why this

- **The owner's time is the scarcest resource.** Every hour on admin is an hour not serving customers.
- **The data already exists** (till sales, invoices, local events, weather), but nobody has time to connect it.
- **AI agents can now act, not just answer.** They can read a photo of an invoice, reason over months of sales and draft the follow-up, which makes "a co-founder in your pocket" possible for the first time.

## How CoFoundr solves it

| Job | What CoFoundr does |
|---|---|
| **Invoice chaser** | Owner snaps an invoice photo on WhatsApp → Gemini extracts supplier, amount, due date and line items → overdue invoices are flagged → escalating reminders are drafted for approval |
| **Revenue forecaster** | Baseline forecast from till or card-payment history, adjusted by Gemini for weather, events and TfL disruptions, with a plain-English reason ("+28% Sat: market nearby + sunny") |
| **Self-grading forecast** | Grades its own forecasts against actual takings, learns checked lessons, and earns (or loses) how far Gemini may move the baseline |
| **Board meeting** | Gemini reads the whole business in one call (every till line, invoice, approval, forecast) and writes a memo; code recomputes every number it claims |
| **Card fee audit** | Reads the card provider's fee summary from a photo and checks every card payment against it; overcharges become a task |
| **Approval flow** | Every finance action waits in an Approve / Edit / Reject queue, in the dashboard or by replying on WhatsApp |
| **Auto-prioritise & schedule** | Ranks open tasks by impact, with a one-line reason each, and builds a weekly plan |
| **Morning brief** | Owner asks on WhatsApp, "what's my day?", and gets the top priorities, forecast and pending approvals |

**Stack:** MongoDB Atlas (single source of truth + search) · Gemini 4 Argon (multimodal extraction + reasoning) · Twilio (WhatsApp) · Treg (tool gateway) · FastAPI web app on Fly.io (dashboard + WhatsApp webhook)

## Architecture

Invoices and requests come in from **Gmail, the dashboard, WhatsApp (via Twilio) and external APIs**. The CoFoundr app on **Fly.io** uses **Gemini** to think and **MongoDB Atlas** to remember. Results go out to the **dashboard** and to **WhatsApp via Twilio**, and nothing involving money happens without the owner's approval.

```
        INPUTS                     PROCESS (on Fly.io)                  OUTPUTS
┌──────────────────────┐                                       ┌───────────────────────┐
│ Gmail (autonomous)   │──┐                                ┌──►│ Dashboard (Fly.io)     │
│  invoices by email   │  │    ┌────────────────────────┐  │   │  approval queue, log   │
├──────────────────────┤  │    │  CoFoundr app          │  │   ├───────────────────────┤
│ Dashboard upload     │──┼───►│  • reads + checks      │──┤   │ WhatsApp via Twilio    │
│  (fallback)          │  │    │  • finds overdue       │  ├──►│  • automatic updates   │
├──────────────────────┤  │    │  • drafts actions      │  │   │  • answers (Gemini)    │
│ WhatsApp via Twilio  │──┤    │  • applies approvals   │  │   │  • "✅ approved" replies│
│  "approve 1", asks   │  │    └───┬──────────────┬─────┘  │   └───────────────────────┘
│  invoice photos      │  │        ▼              ▼        │
├──────────────────────┤  │   ┌─────────┐   ┌──────────┐   │   Nothing involving money
│ APIs (MVP2)          │──┘   │ Gemini  │   │ MongoDB  │   │   leaves without the
│  weather, events,    │      │ thinks  │   │ remembers│   │   owner's approval.
│  Treg tools          │      └─────────┘   └──────────┘   │
└──────────────────────┘                                   │
```

| Piece | Role | Status |
|---|---|---|
| **Gmail** | Invoices arrive by email and are picked up automatically | ✓ Live |
| **Dashboard upload** | Manual fallback for adding an invoice | ✓ Built |
| **WhatsApp via Twilio (in)** | Owner replies "approve 1" or asks a question (Gemini answers, read-only); signed webhook `/whatsapp/twilio` | ✓ Built, going live |
| **WhatsApp via Twilio (out)** | Owner is messaged automatically when something needs them (`/updates` pushed) | ✓ Built, going live |
| **WhatsApp photos** | Owner sends invoice photos on WhatsApp → read and checked | ✓ Built, going live |
| **Card payments** | EVO CSV upload on `/sales`; Square + SumUp read-only. Card data stripped, ids salted-hashed | ✓ Built |
| **APIs** | Weather (Open-Meteo) and TfL disruptions feed the forecast; events + Treg tools next | Weather + TfL ✓ Built |
| **Gemini 4 Argon** | Reads and double-checks invoices and fee summaries, adjusts and grades the forecast, ranks priorities, holds the board meeting, drafts reminders | ✓ Built |
| **MongoDB Atlas** | Every collection (sales as time-series), Atlas Search, Vector Search, Hybrid Search, agent activity log | ✓ Built |
| **Fly.io** | Hosts the app: dashboard + WhatsApp webhook at https://cofoundr.fly.dev | ✓ Live |

## How it's different

| | Accounting tools | General AI chatbots | Hiring help | **CoFoundr** |
|---|---|---|---|---|
| Acts proactively | Partly (rules you set up) | ✗ waits to be asked | ✓ | **✓ spots what needs doing** |
| Connects sales, invoices, events and weather | ✗ | ✗ | Sometimes | **✓** |
| Explains its reasoning | ✗ | Sometimes | ✓ | **✓ every ranking and forecast** |
| Owner approves money actions | n/a | ✗ | Trust-based | **✓ always, by design** |
| Works where the owner already is (WhatsApp) | ✗ | ✗ | ✓ | **✓** |
| Cost | Subscription | Subscription | £££ salary | **Small-business affordable** |

CoFoundr isn't a chatbot or a bookkeeping tool. It's an agent that does the work end to end and stops to ask before anything touches money.

---

## Judging criteria: how we meet them

> ⏳ **To be completed after building.** Each item will link to the feature, the code and the timestamp in the demo video. The target criteria are tracked in `.claude/winning-criteria.md`.

### Agents That Ship
- **Does the job:** _TBD_
- **Reliability:** _TBD_
- **Usefulness:** _TBD_
- **Technical depth:** _TBD_
- **Safety and control:** _TBD_

### The Real World
- **Real-world usefulness:** _TBD_
- **How well it reads the world:** _TBD_
- **Technical execution:** _TBD_
- **Responsibility:** _TBD_

### MongoDB Atlas
| Atlas feature | Used? | Where |
|---|---|---|
| Atlas Search | ✓ | `backend/db/search.py`, index `invoices_text` (typo-tolerant), used by the invoice price check |
| Atlas Vector Search | ✓ | `similar_invoices()`, index `invoices_semantic`: similar past invoices for the cross-supplier price check |
| Hybrid Search | ✓ | `search_invoices()`: keyword + meaning merged with `$rankFusion` |
| Automated Embedding | ✓ | `autoEmbed` on invoices' `search_text`: Atlas embeds on insert, no embedding code of ours |
| Atlas Stream Processing | ✗ not built | |
| Voyage AI | ✓ | `voyage-4`, behind Automated Embedding |

### Gemini 4 Argon
- **Working live demo (50%):** _TBD_
- **Problem and usefulness (25%):** _TBD_
- **Originality / needs Argon (15%):** _TBD_
- **Clarity of pitch (10%):** _TBD_

### WhatsApp (Twilio + Gemini)
- **Acts on its own (before → after):** _TBD_
- **Human control:** _TBD_

---

## User guide

> ⏳ **Draft.** Steps will be confirmed once the features are built.

### For the business owner (WhatsApp)
1. **Add an invoice:** send a photo or PDF of the invoice to CoFoundr on WhatsApp. It replies with what it read; correct anything that's wrong.
2. **Get your morning brief:** message "brief" (or "what's my day?"). You get today's top priorities, the sales forecast and anything waiting for your approval.
3. **Approve an action:** reply "approve", "edit" or "reject" to a drafted reminder. Nothing involving money is sent without your approval.

### For the business owner (dashboard)
1. Open the dashboard (link: _TBD_).
2. **Priorities:** ranked tasks, each with a one-line reason.
3. **Invoices:** all invoices, with overdue ones highlighted. Search by supplier or item.
4. **Forecast:** expected sales for the coming days, with Gemini's explanation.
5. **Approvals:** the queue of drafted actions. Approve, edit or reject.

### For baristas (voice orders, if built)
1. Tap record and let the customer order as normal.
2. CoFoundr fills in the order (items, quantities, extras).
3. **Check it and tap confirm.** Nothing is saved until you confirm.

### Running it locally (for judges)
**Live demo:** https://cofoundr.fly.dev

```bash
git clone https://github.com/sunilramjali/cofoundr.git && cd cofoundr
cp .env.example .env                  # fill in MONGODB_URI, GEMINI_API_KEY, BRIEF_TOKEN
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m data.seed         # load synthetic GAIA Coffee data into MongoDB Atlas
.venv/bin/python -m backend.ai.overdue  # find overdue invoices, draft actions for approval
.venv/bin/uvicorn bridge.main:app --reload   # dashboard at http://localhost:8000
```

Deploy: `fly deploy` (from the repo root).

---

## Team
- **Sunil**
- **Ceyda**
