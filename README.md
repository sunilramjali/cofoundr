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

**Stack:** MongoDB Atlas (single source of truth + search) · Gemini 4 Argon (multimodal extraction + reasoning) · Twilio (WhatsApp) · FastAPI web app on Fly.io (dashboard + WhatsApp webhook)

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
| **WhatsApp via Twilio (in)** | Owner replies "approve 1" or asks a question (Gemini answers from the data and can draft, never approve or send); signed webhook `/whatsapp/twilio` | ✓ Built, going live |
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

> Evidence = the code that does it + the step in the live demo (`docs/demo-script.md`). Video timestamps: _added after recording_.

### Agents That Ship
- **Does the job:** an invoice goes from inbox to approved reminder with no human step until the yes. Gmail is polled every minute (`backend/inputs/gmail.py`), or a WhatsApp photo arrives (`POST /whatsapp/twilio`). Then `pipeline.ingest_invoice` reads it with Gemini, checks the reading, saves it and runs the overdue check, which drafts an escalating reminder as a pending approval. The owner is messaged unprompted (`/updates` pusher) and replies "approve N" (`approvals.decide`).
- **Reliability:** it stops and asks instead of guessing. If the line items don't add up to the total, or a due date doesn't make sense, the invoice is flagged for review and not chased (`backend/ai/extract.py`). Gemini 429s are retried with backoff. If Gemini is down, the reply is a template or a plain-data answer, and that's logged, never silent. Anything that can run twice uses atomic counters and unique indexes. 411 offline scenario tests run in CI (`tests/`), and the bug review that led to them is in `docs/postmortem-2026-10-24.md`.
- **Usefulness:** the jobs a one-person business never has time for: chasing unpaid invoices, catching supplier price rises (`search.price_check` in `backend/db/search.py`), auditing card fees (`backend/ai/fees.py`), forecasting tomorrow's takings with reasons, and "what if I put flat whites up 20p?" answered from the till.
- **Technical depth:**
  - **Tools:** Gemini function calling over MongoDB, with 11 tools in `backend/outputs/assistant.py`. Code does every sum.
  - **Memory:** MongoDB holds the full history, plus the forecaster's own lessons in `lessons`.
  - **Planning:** ranked priorities with a reason each (`backend/ai/prioritise.py`).
  - **Evaluation:** every Gemini output is checked by code before it's trusted: extraction totals, forecast factors, board-memo numbers recomputed from MongoDB, and the fee summary checked against its own worked example. Forecasts are graded against actual takings, and the grades decide how far Gemini may move the next forecast (`backend/ai/review.py`).
- **Safety and control:**
  - Every money action is a pending approval. The only place a decision is applied is `backend/approvals.py`.
  - "approve 4" / "reject 2" are parsed exactly, never by Gemini, and negations are refused.
  - The assistant's one writing tool (`propose_reminder`) can only *draft* into the queue, under the same rules as the automatic overdue check.
  - Twilio signatures are verified and only the owner's number is acted on. Approvals are POST only.
  - Every step, tool call, rejected webhook and failed send is in `agent_log`, shown on `/activity`.

### The Real World
- **Real-world usefulness:** a real user and a real problem: GAIA Coffee's owner (East London coffee truck) does the admin after closing. The demo runs on clearly labelled synthetic data shaped like GAIA's real till, card and invoice exports.
- **How well it reads the world:** photos and PDFs of invoices, including a deliberately messy one whose total doesn't add up (flagged, not guessed). It also reads a card provider's fee summary image. The forecast uses Open-Meteo weather and TfL disruptions near the pitches (`backend/conditions.py`).
- **Technical execution:** live on Fly.io (https://cofoundr.fly.dev): Gemini 4 Argon, MongoDB Atlas, Twilio WhatsApp, Gmail IMAP, Open-Meteo and TfL in one app, deployed by CI/CD on every release.
- **Responsibility:**
  - Uncertainty is shown, not hidden: extraction confidence, forecast ranges, "an estimate" labels, and ⚠️ on any amount the assistant can't match to the records.
  - Card data is stripped on import and payment ids are salted hashes (`backend/payments.py`).
  - Real business data is refused by the demo database (`docs/privacy.md`).

### MongoDB Atlas
| Atlas feature | Used? | Where |
|---|---|---|
| Atlas Search | ✓ | `backend/db/search.py`, 5 indexes. `invoices_text`: typo-tolerant keyword search, **highlighting** of the matched words, **facets** (`$searchMeta`: invoices per supplier on Finances), **autocomplete** in the search box (`GET /search/suggest`), and the **duplicate-invoice check** on intake ("EDC 7781" = "EDC-7781", held for review). `products_text`: typo-tolerant product names for the assistant's price what-ifs ("capuccino" → Cappuccino). `agent_log_text`: search the agent's own record (Activity page + the assistant's `search_activity` tool). `board_memos_text` + `events_text`: the assistant's `search_notes` tool. Live self-test: `python -m backend.db.search --check` |
| Atlas Vector Search | ✓ | `similar_invoices()`, index `invoices_semantic`: similar past invoices for the cross-supplier price check |
| Hybrid Search | ✓ | `search_invoices()`: keyword + meaning merged with `$rankFusion` |
| Automated Embedding | ✓ | `autoEmbed` on invoices' `search_text`: Atlas embeds on insert, no embedding code of ours |
| Atlas Stream Processing | ✗ not built | |
| Voyage AI | ✓ | `voyage-4`, behind Automated Embedding |

### Gemini 4 Argon
- **Working live demo (50%):** a 3-minute live run (`docs/demo-script.md`): invoice photo on WhatsApp → read and checked by `gemini-4-argon` → unprompted WhatsApp message → "approve N" → the board memo → the self-graded forecast. **Verification loops** everywhere: extraction totals, forecast factors, the board memo's numbers, the fee summary's worked example, and the assistant's £ amounts checked against its tool results.
- **Problem and usefulness (25%):** a named user, GAIA Coffee's owner, who can't hire an ops manager or a bookkeeper. The same agent works for any small business with a till, a card machine and supplier invoices.
- **Originality / needs Argon (15%):** the **board meeting** (`backend/ai/board.py`) puts every till line, invoice, approval, forecast, weather day and agent action into **one ~169k-token call** using Argon's 1M context. Code then recomputes every number Gemini claims and corrects or withdraws the wrong ones. People stay in control: nothing that moves money happens without the owner's exact "approve N".
- **Clarity of pitch (10%):** no slides: the owner's phone and the dashboard, live.

### WhatsApp (Twilio + Gemini)
- **Acts on its own (before → after):** before, unpaid invoices sit in an inbox. After, CoFoundr reads each one, flags the overdue ones and messages the owner without being asked: "Reminder drafted for Hackney Hub, reply approve 4" (`backend/outputs/whatsapp.py`, every `WHATSAPP_PUSH_SECONDS`). A Twilio failure delays a message but never loses it.
- **Human control:** CoFoundr reads, drafts and updates on its own, and stops before any money action. Decisions are exact replies, never interpreted by Gemini. Everything else goes to the Gemini assistant, which can look anything up and draft, but never approve, pay or send.

---

## User guide

### For the business owner (WhatsApp)
1. **Join once:** send the sandbox's `join <code>` to CoFoundr's WhatsApp number (Twilio sandbox, +1 415 523 8886).
2. **Add an invoice:** send a photo or PDF. CoFoundr replies with what it read ("Added … checked ✓"), or asks you to check it if it doesn't add up.
3. **Get told, unprompted:** new invoices, drafted reminders and bills to pay arrive as messages, each with its number.
4. **Decide:** reply `approve 4` or `reject 4`. Nothing involving money happens without that reply.
5. **Ask anything:** "what needs doing?", "who owes me?", "what if I put flat whites up 20p?", "is Saturday worth trading?", "chase Shoreditch" (drafts a reminder for your approval). Answers come from your data, and every £ figure is checked against it.

### For the business owner (dashboard: https://cofoundr.fly.dev)
1. **Home:** what CoFoundr did while you were away, what to do first (ranked, with a reason each), and the chat bar (the same assistant as WhatsApp; also on **Ask**).
2. **Inbox:** upload an invoice by hand, or "Check inbox now" for the Gmail inbox.
3. **Tasks:** the approval queue: Approve / Edit / Reject each drafted action.
4. **Finances:** every invoice; search by supplier, product or meaning (Atlas hybrid search).
5. **Insights:** the forecast with Gemini's reasons, how past forecasts did, card payments (upload the EVO CSV) and the card fee audit (upload the fee summary).
6. **Inventory:** what's probably on the truck, from the last stock count (an estimate: deliveries aren't recorded yet).
7. **Board:** the board memo, with every number checked against your records.
8. **Activity:** everything the agent did, step by step.

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

## What's next

- **Staff scheduling:** baristas at £16/hour, rostered from the forecast (e.g. two on days over ~£600), with the day's wage cost next to its takings. Shift offers are drafted as approvals and sent on WhatsApp only after "approve N", because wages are money.
- **The real trading week:** the synthetic till trades Mon/Wed/Fri/Sat; GAIA's real pattern is Thursday–Sunday.
- **Stock that stays right:** weekly counts, plus deliveries read from supplier invoices ("Oat milk, 1L × 36"), so stock = last count + deliveries − usage.
- **Real data, kept apart:** a business's real card and till exports go into its own database (never the demo one), and the forecast's error is measured on its real days.
- **Atlas Stream Processing:** react to new invoices and orders as they land instead of polling. **Gemini background mode** for the board meeting's long read.

## Team
- **Sunil**
- **Ceyda**
