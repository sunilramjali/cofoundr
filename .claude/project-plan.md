# CoFoundr — Project Plan

**Event:** Encode London Hackathon 2026 (23–25 Oct), Encode Hub, Shoreditch
**Submission deadline:** Sunday 25 Oct, 12:00
**Team:** Sunil (solo for now; Jade (Ceyda) joins later)
**Targets:** Main prize + MongoDB, Gemini and Saint sponsor bounties

---

## 1. Pitch

CoFoundr is an AI co-founder for small business owners who can't afford to hire a team. It watches the business's data, spots what needs doing, ranks it by impact and drafts the actions. The owner stays in control: **every finance/money action needs owner approval first.**

Framed for small businesses generally. Demo business: **GAIA Coffee** (East London mobile coffee truck), using **synthetic data** clearly labelled as such.

## 2. Stack

| Layer | Tool | Role |
|---|---|---|
| Database | MongoDB Atlas | Single source of truth: invoices, sales, products, events, tasks, approvals |
| AI | Gemini | Multimodal extraction, reasoning, prioritisation, forecast adjustment, drafting |
| Conversational layer | Saint (saint.work) | Owner talks to CoFoundr on WhatsApp (input + output) |
| Web app: dashboard + Saint bridge | FastAPI on Fly.io | **One app.** Server-rendered HTML dashboard (owner's control panel + approval queue) **and** the endpoints Saint uses. Sits between Saint, Gemini and MongoDB. (Streamlit dropped.) |

## 3. Architecture

```
Owner ⇄ WhatsApp ⇄ Saint ⇄ Fly.io web app (dashboard + /brief, /approve) ⇄ MongoDB Atlas
                                        ⇅
                                      Gemini
```

- Approvals can happen in the dashboard or by WhatsApp reply. Both update the same MongoDB record.
- If Saint has no API/webhook: Saint reads a public read-only `/brief` page (secret token in URL, demo data only). Fallback: a Google Sheet bridge.

### 3a. Bridge strategy (Saint ⇄ MongoDB)

**What it is:** one FastAPI web app in `bridge/`, deployed on **Fly.io** at a public URL (e.g. `https://cofoundr.fly.dev`). It serves **both** the owner's dashboard (server-rendered HTML pages) **and** the endpoints Saint uses, and it's the only thing that talks to Gemini and MongoDB. It's the **only** way Saint reads or changes data. Saint never connects to MongoDB directly.

**Confirmed:** Saint can interact with the Fly.io web app (bridge between Saint, Gemini and the web page).

**Why:**
1. One server-rendered app → the owner's dashboard and Saint read the same pages/data; no JS-only frontend Saint can't read.
2. Security → Saint holds only a demo token (`BRIEF_TOKEN`), never the MongoDB password.
3. Rules in one place → approval checks live in the bridge, so Saint can't bypass them (trust story + "human control" judging criteria).
4. Saint gets finished answers (ranked, explained, WhatsApp-ready), not raw documents.

```
                 ┌─ Dashboard pages (/, /invoices, /approvals) ◄── Owner (browser)
MongoDB ◄──► Fly.io web app (FastAPI)
                 └─ Saint endpoints (/brief, /approve) ◄──► Saint ◄──► WhatsApp ◄──► Owner
                        ⇅
                      Gemini
```

**Endpoints:**

| Endpoint | Called when | Does |
|---|---|---|
| `GET /brief?token=…&format=html\|json` | Owner asks Saint for the brief | Reads tasks, invoices, forecasts, approvals → returns a WhatsApp-ready message |
| `POST /approve` `{id, decision}` | Owner replies "approve 1" / "reject 1" | Checks the action exists and is `pending` → updates `approvals` (decision, `decided_via: "whatsapp"`, timestamp) → dashboard reflects it |
| `POST /invoice` (later) | Owner sends an invoice photo | Sends the photo to Gemini for extraction → saves to `invoices` |
| `GET /health` | Fly.io health check | Returns `ok` |

**Rules:**
- Approvals are `POST` only, never a clickable GET link (link previews/crawlers could trigger them).
- Every bridge call is logged to an agent activity record in MongoDB (Agents That Ship: "clear record").
- Dashboard pages and Saint endpoints live in the same app and import collection names/shapes from `backend/db/schema.py`.
- Dashboard pages are plain server-rendered HTML (Jinja templates, optional htmx), so Saint can read them too.
- The token in the URL is OK only because all data is synthetic demo data.

**Fly.io deployment:**
- `bridge/` holds `main.py`, `requirements.txt`, `Dockerfile` and `fly.toml` (app `cofoundr`, region `lhr`, internal port 8080, health check on `/health`).
- Secrets are set with `fly secrets set MONGODB_URI=… BRIEF_TOKEN=… GEMINI_API_KEY=…` and are never committed.
- Deploy: `cd bridge && fly deploy`.
- MongoDB Atlas network access must allow Fly.io's outbound IPs (or `0.0.0.0/0` for the hackathon, demo data only).
- **Deploy a stub first** (hard-coded `/brief` text) and test that Saint can read it **before** building real logic.

**Saint integration:** confirmed Saint works through the Fly.io web app → build `/brief` + `/approve`. Still to confirm: can Saint pass a WhatsApp photo to `POST /invoice`? (Until then, invoices are uploaded in the dashboard.) Google Sheet fallback no longer needed.

## 4. Modules (priority order)

**Must ship**
1. **Invoice chaser** — ingest invoices, flag overdue, draft escalating reminders (approval-gated).
   Inputs, in build order: WhatsApp photo → PDF upload → email (forward to Saint's inbox) → manual form. Gemini extracts supplier, amount, due date and line items to JSON.
2. **Revenue forecaster** — Python baseline (statsmodels/Prophet) on POS history; Gemini adjusts for events + weather and explains why (e.g. "+28% Sat: market nearby + sunny").
3. **Approval flow** — Approve / Edit / Reject queue for all finance actions. Never cut this — it's the trust story.
4. **Auto-prioritise & schedule** — Gemini ranks open tasks via function calling over MongoDB, with a one-line reason each; produces a weekly plan.

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

## 6. Data
- **POS:** synthetic GAIA sales, ~6 months (product, qty, price, timestamp, location)
- **Invoices:** synthetic set incl. a few overdue + sample photos/PDFs for the demo
- **Events:** via API (e.g. Ticketmaster Discovery)
- **Weather:** Open-Meteo (free, no key)
- **Foot traffic:** parked for now (option later: TfL station entry/exit open data)

### MongoDB collections
`businesses`, `products`, `sales`, `invoices`, `events`, `forecasts`, `tasks`, `approvals`

## 7. Timeline

> Sunil is solo for now and takes both columns. Jade (Ceyda) picks up the app/Saint column when she joins.

| When | Backend, data, Gemini (Sunil) | Web app, Saint (Sunil for now → Jade) |
|---|---|---|
| Sat 10:30–13:00 | Atlas schema; synthetic POS + invoices | FastAPI skeleton on Fly.io with `/brief`; test Saint can read it |
| 13:00–17:00 | Gemini invoice extraction; forecast baseline + Gemini adjustment | Dashboard pages on Fly.io (priorities, invoices, forecast); approval queue → MongoDB |
| 17:00–21:00 | Events API; Gemini prioritisation | Saint flow: photo in → brief out → "approve" reply updates MongoDB |
| 21:00–late | Waste estimator; reminder drafts | Heatmap; end-to-end test |
| Sun → 12:00 | Bug fixes; seed clean demo state | Backup demo video; README; submit |

## 8. Demo flow
1. Owner sends an invoice photo to Saint on WhatsApp → appears extracted in dashboard.
2. Owner asks Saint for the morning brief → Saint pulls `/brief` and replies.
3. Dashboard shows forecast with Gemini's reasoning + prioritised tasks.
4. Owner replies "approve" on WhatsApp → reminder status updates live in dashboard.

## 9. Open questions
- [ ] Ask Saint team: can Saint pass a WhatsApp photo to our backend, or only Drive/email?
- [x] Saint access: Saint interacts with our Fly.io web app (bridge)
- [ ] Confirm GAIA owner is OK with the name being used
- [x] Front end: server-rendered dashboard in the FastAPI app on Fly.io (Streamlit dropped)
- [ ] Gemini 4 Argon API key (Sunil adding)
