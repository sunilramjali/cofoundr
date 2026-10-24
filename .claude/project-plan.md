# CoFoundr — Project Plan

**Event:** Encode London Hackathon 2026 (23–25 Oct), Encode Hub, Shoreditch
**Submission deadline:** Sunday 25 Oct, 12:00
**Team:** Sunil (@sunilramjali) + Jade / Ceyda (@JadeAlpay)
**Targets:** Main prize + MongoDB and Gemini sponsor bounties

> **Sat 24 Oct, 22:45: Saint dropped.** Saint can't POST to our app, so the owner's WhatsApp channel is now **Twilio** (WhatsApp sandbox) and the conversation itself is **Gemini**. No part of CoFoundr depends on Saint any more.

---

## 1. Pitch

CoFoundr is an AI co-founder for small business owners who can't afford to hire a team. It watches the business's data, spots what needs doing, ranks it by impact and drafts the actions. The owner stays in control: **every finance/money action needs owner approval first.**

Framed for small businesses generally. Demo business: **GAIA Coffee** (East London mobile coffee truck), using **synthetic data** clearly labelled as such.

## 2. Stack

| Layer | Tool | Role |
|---|---|---|
| Database | MongoDB Atlas | Single source of truth: invoices, sales, products, events, tasks, approvals |
| AI | Gemini | Multimodal extraction (invoices, card fee summaries), reasoning, prioritisation, forecast adjustment + lessons from its own grades, the 1M-context board meeting, drafting, **and the owner's conversation** (read-only assistant on WhatsApp and in the dashboard chat bar) |
| WhatsApp channel | Twilio (WhatsApp sandbox) | Owner talks to CoFoundr on WhatsApp: messages and invoice photos in (signed webhook), replies and unprompted updates out (REST API) |
| Web app | FastAPI on Fly.io | **One app.** Server-rendered HTML dashboard (owner's control panel + approval queue), the Twilio webhook and the background workers (Gmail poll, WhatsApp push). The only thing that talks to Gemini, Twilio and MongoDB. (Streamlit dropped.) |

## 3. Architecture

```
Owner ⇄ WhatsApp ⇄ Twilio ⇄ Fly.io web app (dashboard + /whatsapp/twilio + pushers) ⇄ MongoDB Atlas
                                        ⇅
                                      Gemini
```

- Approvals can happen in the dashboard or by WhatsApp reply. Both go through `approvals.decide` and update the same MongoDB record.
- "approve 4" / "reject 2" are parsed exactly, **never by Gemini**. Anything else the owner types goes to the Gemini assistant, which can read the data but never change it.

### 3a. Web app + WhatsApp (Twilio ⇄ MongoDB)

**What it is:** one FastAPI web app in `bridge/`, deployed on **Fly.io** at `https://cofoundr.fly.dev`. It serves the owner's dashboard, receives WhatsApp messages from Twilio and sends replies and updates back. Twilio never touches MongoDB; it only relays messages.

**Why:**
1. Rules in one place → approval checks live in the app, so no channel can bypass them (trust story + "human control" judging criteria).
2. Security → Twilio signs every webhook call (`X-Twilio-Signature`, checked against `TWILIO_AUTH_TOKEN`), and only the owner's number (`OWNER_WHATSAPP`) is acted on; anyone else is logged and ignored.
3. Proactive, not a chatbot → the app pushes new invoices, drafts and decisions to the owner's phone on its own.

```
                 ┌─ Dashboard pages (/, /invoices, /approvals, /ask) ◄── Owner (browser)
MongoDB ◄──► Fly.io web app (FastAPI)
                 └─ POST /whatsapp/twilio (in) + REST send (out) ◄──► Twilio ◄──► WhatsApp ◄──► Owner
                        ⇅
                      Gemini
```

**WhatsApp flows** (`backend/inputs/whatsapp.py` in, `backend/outputs/whatsapp.py` out):
| Owner sends | CoFoundr does | Reply |
|---|---|---|
| Invoice photo/PDF | `pipeline.ingest_invoice(source="whatsapp_photo")` | The pipeline's result ("Added … checked ✓" or "needs your check") |
| "approve 4" / "reject 2" | `approvals.decide(via="whatsapp")` | "✅ #4 approved: …" (or why it couldn't) |
| Anything else | Gemini assistant (`backend/outputs/assistant.py`), read-only | Gemini's answer from the data |
| *(nothing: unprompted)* | Every `WHATSAPP_PUSH_SECONDS` (30), new `/updates` messages are sent | "New invoice from Eastside Dairy…", "Reminder drafted, reply approve 4" |

**WhatsApp 24-hour rule:** WhatsApp only delivers free-form messages within 24 h of the owner's last message, so before the demo the owner sends "hi" to the sandbox number.

**Endpoints:**

| Endpoint | Called when | Does |
|---|---|---|
| `POST /whatsapp/twilio` | Twilio delivers a WhatsApp message | Checks the signature + sender → answers Twilio at once, handles the message in the background, sends the reply |
| `POST /ask` | Owner types in the dashboard chat bar | Same rules as WhatsApp: exact decisions, otherwise the Gemini assistant |
| `GET /brief?token=…&format=html\|json` | Owner asks for the brief | Reads tasks, invoices, forecasts, approvals → returns a WhatsApp-ready message |
| `GET /updates?token=…[&since=…][&format=text][&peek=true]` | The WhatsApp pusher (and any other consumer) | WhatsApp-ready messages for anything new: new invoices (or "needs your check"), new drafts with "reply approve N", dashboard decisions. Each consumer has its own cursor |
| `GET /health` | Fly.io health check | Returns `ok` |

**Rules:**
- Approvals are `POST` only, never a clickable GET link (link previews/crawlers could trigger them).
- Every action is logged to `agent_log` in MongoDB (Agents That Ship: "clear record"), including rejected webhooks and failed sends.
- Dashboard pages import collection names/shapes from `backend/db/schema.py`.
- Dashboard pages are plain server-rendered HTML (Jinja templates).
- The token in the URL is OK only because all data is synthetic demo data.

**Fly.io deployment:**
- **Live: https://cofoundr.fly.dev** (app `cofoundr`, region `lhr`, internal port 8080, health check on `/health`, 1 machine kept warm).
- `Dockerfile` and `fly.toml` live at the **repo root** (the image needs `backend/` as well as `bridge/`). Access logging is off so the `/brief` token never appears in `fly logs`.
- Secrets are set with `fly secrets set MONGODB_URI=… BRIEF_TOKEN=… GEMINI_API_KEY=… TWILIO_ACCOUNT_SID=… TWILIO_AUTH_TOKEN=… TWILIO_WHATSAPP_FROM=… OWNER_WHATSAPP=…` and are never committed. Non-secret config (`GEMINI_MODEL`, `MONGODB_DB`, `PUBLIC_URL`, `GMAIL_POLL_SECONDS`, `WHATSAPP_PUSH_SECONDS`) is in `fly.toml`.
- Deploy: `fly deploy` from the repo root.
- MongoDB Atlas network access must allow Fly.io's outbound IPs (or `0.0.0.0/0` for the hackathon, demo data only).

**Twilio setup:** WhatsApp sandbox (Console → Messaging → Try it out → Send a WhatsApp message). Sender `TWILIO_WHATSAPP_FROM=+447723317807` (our account's sandbox number, shown on that page; not the US `+14155238886` some accounts get); the owner's phone joins with `join twilio-trial`. Sandbox "When a message comes in" → `https://cofoundr.fly.dev/whatsapp/twilio` (POST). `PUBLIC_URL` must match that URL or signatures won't verify. Local dev (`COFOUNDR_SANDBOX=1`) never sends WhatsApp messages.

## 4. Modules (priority order)

**Must ship**
1. **Invoice chaser** — ingest invoices, flag overdue, draft escalating reminders (approval-gated).
   Inputs, in build order: **Gmail inbox, autonomous (MVP1)** → dashboard upload, manual fallback (built) → WhatsApp photo via Twilio (built, MVP2). Gemini extracts supplier, amount, due date and line items to JSON.
   **Gmail intake design:** a dedicated demo Gmail account (not anyone's personal inbox) with 2-step verification and an **app password**; the Fly.io app polls it over IMAP about every minute, processes new emails with PDF/image attachments once (tracked by Message-ID), labels them processed, and logs each step in `agent_log`. Secrets: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (Fly secrets + `.env`).
2. **Revenue forecaster** — Python baseline (statsmodels/Prophet) on POS history; Gemini adjusts for events + weather and explains why (e.g. "+28% Sat: market nearby + sunny").
3. **Approval flow** — Approve / Edit / Reject queue for all finance actions. Never cut this — it's the trust story.
4. **Auto-prioritise & schedule** — Gemini ranks open tasks via function calling over MongoDB, with a one-line reason each; produces a weekly plan.

**Built beyond the original plan (Sat night, Sunil, live since release #43)**
- **Card payments in:** EVO CSV upload on `/sales` (`POST /import/evo`; EVO has no merchant API), Square + SumUp read-only connectors (`backend/inputs/evo|square|sumup.py` → `backend/payments.py record_payments()`). Card data stripped, salted-hash ids, idempotent. The forecast uses card takings when there are no till orders.
- **Self-grading forecast** (`backend/ai/review.py`): stored forecasts graded against actual takings; Gemini writes ≤3 checked lessons that go into the next forecast; Gemini's adjustment cap is **earned** (±10/20/35% by how often its adjustments helped). `--backtest N` replays past days with observed weather. Collections `forecast_reviews`, `lessons`.
- **Board meeting** (`backend/ai/board.py`, page `/board`): Gemini reads every till line, invoice, approval, forecast, weather, transport and agent action in **one ~169k-token call** (Argon's 1M context); every number in the memo is a typed claim that code recomputes from MongoDB, wrong ones are corrected or withdrawn. Rate-limited (one per 10 min). Collection `board_memos`.
- **Card fee audit** (`backend/ai/fees.py`, `POST /import/fees`): Gemini reads the card provider's fee summary image, the page's own worked example checks the reading, then code checks every card payment; overcharges become a `fee_overcharge` task. Collection `fee_audits`.

**Nice to have**
5. **Events heatmap** — events API → map of demand spikes by location/date.
6. **Waste estimator** — sales vs orders → order-cut recommendations with reasons.
7. **Partnership finder** — events/organisers worth partnering with + drafted outreach.
8. **Voice order entry** — Gemini listens to customer orders (audio) and auto-fills them as structured orders (product, qty, modifiers) for the barista. **Barista must double-check and confirm** before the order is saved to MongoDB (same human-in-the-loop principle as approvals).

**Cut order if behind:** voice order entry → partnership finder → waste estimator → heatmap.

## 5. Gemini — show real reasoning
- Multimodal invoice extraction (photo/PDF → structured JSON)
- Forecast adjustment + plain-English explanation
- Task prioritisation with visible reasoning in the UI
- Waste analysis + drafting reminders/outreach
- Board meeting: the whole business in one 1M-context call, every claimed number recomputed by code
- Self-grading: forecasts graded against actual takings, lessons checked against the measured errors
- Fee summary image → rates, checked against the summary's own worked example

## 6. Data
- **POS:** synthetic GAIA till export (Sep–Nov 2026, 3,969 orders) via `backend/sales.py`, loaded up to today
- **Card payments:** synthetic EVO CSV (`data/samples/evo_transactions_synthetic.csv`, with planted fee overcharges) + synthetic fee summary (`data/samples/card_fees_synthetic.png`)
- **Real GAIA data:** never in the repo, never in the demo DB (`cofoundr-db` refuses it). Only in a separate DB (`MONGODB_DB=gaia-real` + `PAYMENT_ID_SALT`); no real figures in logs, tests or docs. A business uses real data only, never mixed with synthetic
- **Invoices:** synthetic set incl. a few overdue + sample photos/PDFs for the demo
- **Events:** via API (e.g. Ticketmaster Discovery)
- **Weather:** Open-Meteo (free, no key)
- **Transport:** TfL disruptions near the pitches during trading hours (`transport`); TfL keeps no history, so ours builds up from now
- **Foot traffic:** parked for now (option later: TfL station entry/exit open data)

### MongoDB collections
`businesses`, `products`, `orders`, `sales` (time-series), `stock_counts`, `payments`, `invoices`, `events`, `weather`, `transport`, `forecasts`, `forecast_reviews`, `lessons`, `tasks`, `approvals`, `fee_audits`, `board_memos`, `agent_log`, `cursors`, `counters` (names live in `backend/db/schema.py`)

## 7. Timeline

> Sunil built MVP1 solo up to Sat ~13:30. Jade (Ceyda) has now joined; split the remaining columns between you.

| When | Backend, data, Gemini (Sunil) | Web app, WhatsApp (Jade) |
|---|---|---|
| Sat 10:30–13:00 | Atlas schema; synthetic POS + invoices | FastAPI skeleton on Fly.io with `/brief` |
| 13:00–17:00 | Gemini invoice extraction; forecast baseline + Gemini adjustment | Dashboard pages on Fly.io (priorities, invoices, forecast); approval queue → MongoDB |
| 17:00–21:00 | Events API; Gemini prioritisation | Gmail intake; WhatsApp via Twilio: photo in → updates out → "approve" reply updates MongoDB |
| 21:00–late | Waste estimator; reminder drafts | Heatmap; end-to-end test |
| Sun → 12:00 | Bug fixes; seed clean demo state | Backup demo video; README; submit |

## 8. Demo flow
1. Owner sends an invoice photo on WhatsApp → CoFoundr replies "Added … checked ✓" → appears extracted in the dashboard.
2. Owner asks on WhatsApp "what's waiting?" → Gemini answers from the data.
3. Dashboard shows forecast with Gemini's reasoning + prioritised tasks.
4. A reminder is drafted → the owner's phone buzzes unprompted → owner replies "approve 4" → status updates live in the dashboard.
5. `/board`: the board memo, every number checked (one struck through and corrected); `/sales`: upload the fee summary → the audit finds the planted overcharges → a task appears in priorities.

**Sunday-morning demo prep** (writes the demo DB, tell Jade first): `python -m backend.ai.review --backtest 21` → `python -m backend.ai.forecast` → `python -m backend.ai.board` (~70 s) → on `/sales` upload the synthetic EVO CSV, then the fee summary PNG → `python -m backend.ai.prioritise`.

## 9. Open questions
- [x] WhatsApp channel: **Twilio WhatsApp sandbox** (Saint dropped Sat 22:45: it couldn't POST to our app)
- [ ] Twilio live: owner's phone joined the sandbox, sandbox webhook set, Fly secrets set (`TWILIO_*`, `OWNER_WHATSAPP` still missing). Code released to `main` with #43 (deployed Sat 24 Oct)
- [ ] Confirm GAIA owner is OK with the name being used
- [x] Front end: server-rendered dashboard in the FastAPI app on Fly.io (Streamlit dropped)
- [x] Gemini 4 Argon API key: working paid-tier key (`AIza…`) since Sat 15:00; lab key (`gcplab.me`) hit its spend cap, a new free-tier key had 0 Argon quota
