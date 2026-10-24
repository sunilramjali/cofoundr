# CoFoundr — Project Plan

**Event:** Encode London Hackathon 2026 (23–25 Oct), Encode Hub, Shoreditch
**Submission deadline:** Sunday 25 Oct, 12:00
**Team:** 2
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
| Dashboard | Streamlit | Visual control panel + approval queue |
| Saint bridge | FastAPI on Fly.io | Plain HTML/JSON endpoints Saint can read (Streamlit is JS-rendered, so Saint likely can't read it) |

## 3. Architecture

```
Owner ⇄ WhatsApp ⇄ Saint ⇄ FastAPI (/brief, /approve) ⇄ MongoDB Atlas ⇄ Streamlit dashboard
                                   ⇅
                                 Gemini
```

- Approvals can happen in the dashboard or by WhatsApp reply. Both update the same MongoDB record.
- If Saint has no API/webhook: Saint reads a public read-only `/brief` page (secret token in URL, demo data only). Fallback: a Google Sheet bridge.

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

| When | Person A — backend, data, Gemini | Person B — app, Saint |
|---|---|---|
| Sat 10:30–13:00 | Atlas schema; synthetic POS + invoices | FastAPI skeleton on Fly.io with `/brief`; test Saint can read it |
| 13:00–17:00 | Gemini invoice extraction; forecast baseline + Gemini adjustment | Streamlit dashboard (priorities, invoices, forecast); approval queue → MongoDB |
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
- [ ] Ask Saint team: API / webhook access for hackathon teams?
- [ ] Confirm GAIA owner is OK with the name being used
- [ ] Final call: Streamlit vs other front end
