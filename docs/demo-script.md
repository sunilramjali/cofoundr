# CoFoundr: demo script (3 min, live, no slides)

For the Gemini judges (3-min live demo + 2-min Q&A), the main tracks, and the backup / MongoDB video.
All data is synthetic. Timings measured Sat 24 Oct on the sandbox: invoice read + checked in **5–11 s**.

**Roles:** one person talks + drives the laptop (dashboard), the other holds the owner's phone (mirrored on screen).

---

## Before you go on (T-30 min)

- [ ] **WhatsApp works end to end today.** Send "hi" from the owner's phone to the sandbox number (opens WhatsApp's 24 h window; the sandbox join also expires after ~72 h: re-send `join <code>` if replies stop). Expect a Gemini reply. If nothing comes back, `/activity` or `agent_log` shows why (`whatsapp_rejected` = signature/URL problem, `whatsapp_send_failed` = Twilio)
- [ ] **Board fix released** (PR #50 → stage → main). Until then, **don't press "Hold a board meeting"**: show the stored memo
- [ ] **Fresh invoices on the phone** (an invoice that's already in CoFoundr just says "already in CoFoundr"):
  - Live demo: `data/samples/gaia_overdue.png` (GAIA-1050, overdue → drafts a reminder) and `data/samples/supplier_messy.png` (BLR-2231, total doesn't add up)
  - Rehearsals and the video: their own kits (GAIA-1051/1052, EDC-7811/7812, BLR-2240/2241), never the live-demo ones
- [ ] Tabs open on https://cofoundr.fly.dev: Home, Inbox (`/inbox`, upload fallback), Tasks (`/approvals`), Board, Insights (`/sales`), Activity
- [ ] Phone mirrored (macOS iPhone Mirroring or QuickTime → New Movie Recording → iPhone), notifications from everything else off, Do Not Disturb except WhatsApp
- [ ] Phone hotspot ready in case venue Wi-Fi drops
- [ ] Backup video open in a tab, ready to play

## The 3 minutes

| Time | On screen | Say (roughly) |
|---|---|---|
| **0:00–0:20** | Home | "This is GAIA Coffee: one truck in East London, one owner who does everything. They can't hire an ops manager or a bookkeeper. CoFoundr is that co-founder. It does the work on its own and **stops and asks before money moves**." |
| **0:20–0:50** | Phone: send `gaia_overdue.png` on WhatsApp | "A customer hasn't paid. The owner just snaps the invoice." → reply ~10 s: "Added GAIA-1050 … checked ✓ (attempt 1). 1 new action drafted". "Gemini read it, then **checked its own reading**: line items add up, the due date makes sense. Only then saved to MongoDB." |
| **0:50–1:10** | Phone buzzes on its own (≤ 30 s) | "I didn't ask. It's telling me it drafted a reminder and needs a yes." → reply **`approve N`** (N from the message) |
| **1:10–1:25** | Tasks → the approval shows "approved via WhatsApp"; Activity | "Same record in MongoDB, whether I approve here or on WhatsApp. 'approve 5' is parsed exactly, **never by the AI**, and every step is in the log." |
| **1:25–1:45** | Phone: "what's waiting for me?" | Gemini answers from the data. "It can read everything but change nothing. Only my approve does that." |
| *(optional, cut if late)* | Phone: send `supplier_messy.png` | "This one's blurry and the total's wrong." → "needs your review: line items add up to 237.00 but the printed total is 263.00". "**It doesn't guess. It asks.**" |
| **1:45–2:25** | Board | "When the owner asks, it holds a board meeting: every till line, invoice, approval and forecast in **one Gemini 4 Argon call, about 170,000 tokens**. But we don't trust it blindly: every number is a claim that code recomputes from MongoDB." Point at a **corrected** insight: "It said 10 pastries, the database said 8: sent back, fixed." |
| **2:25–2:50** | Insights: forecast + "How my forecasts did" | "Gemini adjusts the forecast for rain, events, Tube delays, and explains why. Then it **grades itself** against what we actually took. Its rain cuts made it worse, so it learned 'cut less for rain' and **its own trust cap dropped to ±10%**." Mention the fee audit: "It read the card fee summary and found 4 overcharged payments." |
| **2:50–3:00** | Home | "An agent that does a real job end to end, checks its own work, and always asks before touching money. That's CoFoundr." |

**If something breaks live:** WhatsApp silent → upload the same photo on the dashboard (`/inbox` → upload) and approve in Tasks; say "WhatsApp's 24-hour window, here's the same flow in the dashboard". Gemini slow/429 → it retries and the log says so: show that, it's the reliability story. Everything down → play the backup video.

## Rehearsals

1. **Dry run (laptop only, touches nothing):** `COFOUNDR_SANDBOX=1 .venv/bin/uvicorn bridge.main:app --port 8765`, open http://localhost:8765, upload the rehearsal kit on `/inbox`, approve in Tasks, walk Board and Insights with a timer. Restarting the sandbox resets it.
2. **Full live run with the phone:** rehearsal kit over WhatsApp. This writes to the demo DB (adds GAIA-1051 + its approval): fine, but **tell your partner first**, and approve only the new approval, never #1–#4.
3. Aim for **2:45** so there's slack. Cut the messy invoice first, then the fee-audit line.

## Backup video (also the MongoDB submission: 3 min, must show MongoDB in use)

Record a full live run with the **video kit** (QuickTime screen recording, phone mirrored in a corner). Same script, but trade the Q&A-friendly bits for ~30 s of MongoDB:

- **Atlas UI → Collections:** `sales` is a time-series collection; `agent_log` is the agent's record of every action
- **Atlas UI → Search & Vector Search indexes:** `invoices_text` (Atlas Search) and `invoices_semantic` (Vector Search with **Automated Embedding**, `voyage-4`: Atlas embeds each invoice on insert, no embedding code of ours)
- **Terminal:** `python -m backend.db.search "milk supplier"` → **Hybrid Search** (`$rankFusion`): finds both dairies, including Meadowfield, which never says "supplier" (keyword + meaning)
- `python -m backend.db.search --similar EDC-7781` → Vector Search powering the cross-supplier price check
- Say the names out loud: Atlas Search, Vector Search, Hybrid Search, Automated Embedding, Voyage AI

Upload unlisted to YouTube; put the link in the README and the submission.

## Q&A crib

- **Why Gemini 4 Argon?** The 1M context lets the board meeting read the whole business in one call; multimodal for invoice photos and fee summaries; and every output goes through a verification loop (extraction totals, forecast factors, board numbers, the fee summary's own worked example).
- **What if Gemini is wrong?** It's checked by code before anything is stored or shown as fact; wrong → sent back once → corrected, withdrawn, or shown struck through. Low confidence → the owner is asked.
- **Can someone else approve?** Twilio signs every webhook (checked); only the owner's number is acted on; forged calls are logged and ignored. Approvals are POST only, parsed exactly, never by the AI.
- **Real data?** The demo is synthetic. Real exports (EVO card machine) only go into a separate database; the demo database refuses them. Card data is stripped on import.
- **Who pays?** One-person businesses: the cost of one missed invoice a month.
