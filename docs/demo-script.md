# CoFoundr: demo script (3 min, live, no slides)

For the Gemini judges (3-min live demo + 2-min Q&A), the main tracks, and the backup / MongoDB video.
All data is synthetic. Timings measured Sat 24 Oct on the sandbox: invoice read + checked in **5–11 s**.

**Roles:** one person talks + drives the laptop (dashboard), the other holds the owner's phone (mirrored on screen).

---

## Before you go on (T-30 min)

- [ ] **WhatsApp works end to end today.** Legacy Twilio sandbox (+1 415 523 8886; the +44 trial sandbox can't reply). Send "hi" from the owner's phone to the sandbox number (opens WhatsApp's 24 h window; the sandbox join also expires after ~72 h: re-send `join <code>` if replies stop). Expect a Gemini reply. If nothing comes back, `/activity` or `agent_log` shows why (`whatsapp_rejected` = signature/URL problem, `whatsapp_send_failed` = Twilio)
- [ ] **Assistant tools released** (#57 → release PR → main, Jade approves). Until then, ask "what's waiting for me?" at 1:25, not the what-if
- [ ] Don't press "Hold a board meeting" live (~70 s): show the stored memo
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
| **1:25–1:45** | Phone: **"What if I put flat whites up 20p?"** | ~5 s: "an extra £19.10 a week… you could sell 4.9% fewer and still take the same". "Gemini picked the tool; **code did the sums** from the till, and every £ in the answer is checked against what the tool returned. It can look anything up and draft, but it can't approve, pay or send. Only my approve does that." *(Not released yet → "what's waiting for me?")* |
| *(optional, cut if late)* | Phone: "Chase Shoreditch" | "Already waiting as #N, reply approve N": "it won't chase twice, and it won't send anything itself." |
| *(optional, cut if late)* | Phone: send `supplier_messy.png` | "This one's blurry and the total's wrong." → "needs your review: line items add up to 237.00 but the printed total is 263.00". "**It doesn't guess. It asks.**" |
| **1:45–2:25** | Board | "When the owner asks, it holds a board meeting: every till line, invoice, approval and forecast in **one Gemini 4 Argon call, about 170,000 tokens**. But we don't trust it blindly: every number is a claim that code recomputes from MongoDB." Point at a **corrected** insight: "It said 10 pastries, the database said 8: sent back, fixed." |
| **2:25–2:50** | Insights: forecast + "How my forecasts did" | "Gemini adjusts the forecast for rain, events, Tube delays, and explains why. Then it **grades itself** against what we actually took. Its rain cuts made it worse, so it learned 'cut less for rain' and **it only lets itself move the forecast ±20% now** (helped on 4 of its last 10 days)." Mention the fee audit: "It read the card fee summary and found 4 overcharged payments." |
| **2:50–3:00** | Home | "An agent that does a real job end to end, checks its own work, and always asks before touching money. That's CoFoundr." |

**If something breaks live:** WhatsApp silent → upload the same photo on the dashboard (`/inbox` → upload), approve in Tasks, and ask the question in the Home chat bar; say "WhatsApp's 24-hour window, here's the same flow in the dashboard". **Don't ask about stock live**: the last stock count is weeks old, so the answer is weak. Gemini slow/429 → it retries and the log says so: show that, it's the reliability story. Everything down → play the backup video.

## Rehearsals

1. **Dry run (laptop only, touches nothing):** `COFOUNDR_SANDBOX=1 .venv/bin/uvicorn bridge.main:app --port 8765`, open http://localhost:8765, upload the rehearsal kit on `/inbox`, approve in Tasks, walk Board and Insights with a timer. Restarting the sandbox resets it.
2. **Full live run with the phone:** rehearsal kit over WhatsApp. This writes to the demo DB (adds GAIA-1051 + its approval): fine, but **tell your partner first**, and approve only the new approval, never #1–#4.
3. Aim for **2:45** so there's slack. Cut the messy invoice first, then the fee-audit line.

## Backup video (also the MongoDB submission: 3 min, must show MongoDB in use)

Record a full live run with the **video kit** (QuickTime screen recording, phone mirrored in a corner). Same script, but trade the Q&A-friendly bits for ~30 s of MongoDB:

- **Atlas UI → Collections:** `sales` is a time-series collection; `agent_log` is the agent's record of every action
- **Atlas UI → Search & Vector Search indexes:** `invoices_text` (Atlas Search) and `invoices_semantic` (Vector Search with **Automated Embedding**, `voyage-4`: Atlas embeds each invoice on insert, no embedding code of ours)
- **Finances page search box:** type "eas" → autocomplete suggests Eastside Dairy Co (Atlas autocomplete); search "oat milk" → matched words highlighted (Atlas highlighting); search "milk supplier" → results tagged "by keyword" / "by meaning" (hybrid search). The "Most invoices" chips above are Atlas facets (`$searchMeta`)
- **News page:** "soonest and closest first": Atlas Search `near` on the date and on a geo point (the pitch); filter chips are facets; search "filming" → highlighted. Say: "the same story from two papers is merged with moreLikeThis"
- **Activity page search:** "approved" → every decision, words highlighted (Atlas Search over the agent's own log; "approved" also finds "approval"). The demo log has no supplier names yet, so don't search "Hackney" there
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
- **Can the chat do things?** One tool writes: `propose_reminder` puts a *draft* in the approval queue, under the same rules as the automatic check (not twice, not within 3 days, max 3, not if paid or unchecked). If Gemini claims it "sent" something, code replaces the answer.
- **Is the what-if a promise?** No: it assumes the same number sold and says so; the break-even says how many customers you could lose before the rise stops paying.
