# CoFoundr — Winning Criteria

Source: Encode London 2026 Luma page (luma.com/encode-london-2026), checked Sat 24 Oct.
**Main judging criteria are NOT published there** — they're on the Encode London platform page. See "To confirm" at the bottom.

**Submission:** Sunday 25 Oct, 12:00 (team deadline). Demos, judging and awards follow; event ends 16:30.

---

## Main challenges (tracks)

| Track | Brief | CoFoundr fit | Priority |
|---|---|---|---|
| **Agents That Ship** | An AI agent that does a real job end to end, not just a chatbot | Perfect fit: CoFoundr ingests invoices, forecasts, prioritises and acts (with approval) | **Primary** |
| **The Real World: AI that understands the world around us** | Software that makes sense of the physical world from photos, audio, maps and open data | Partial: invoice photos, events map, weather/open data | Lowest (per team decision) |

### "Agents That Ship" — judging criteria
- **Does the job:** completes a real task from start to finish with minimal hand-holding.
- **Reliability:** handles errors and edge cases, and knows when to stop and ask a human.
- **Usefulness:** someone would actually use it, or pay for it.
- **Technical depth:** good use of tools, memory, planning and evaluation.
- **Safety and control:** sensible permissions, approval steps for risky actions and a clear record of what the agent did.

### How to score on "Agents That Ship"
- Show an **end-to-end job**: invoice photo in → extracted → overdue flagged → reminder drafted → owner approves → status updated.
- Make it clearly **not a chatbot**: the agent acts proactively (morning brief, ranked tasks), not just answering questions.
- Show it **working live**, with a backup video.
- **Reliability:** handle bad inputs visibly (unreadable invoice, missing due date) by asking the owner instead of guessing.
- **Safety and control:** an **agent activity log** in MongoDB (every action, tool call, approval), shown in the dashboard.
- **Technical depth:** show tools (function calling, Treg), memory (MongoDB history), planning (weekly plan) and evaluation (verifying extracted data).

### "The Real World" — full brief (reference only, NOT prioritised)
**Brief:** Build software that makes sense of the physical world from photos, audio, maps and open data. Take AI out of the chat window (streets, venues, parks, trails, wildlife, weather, air quality) by combining vision, audio and language models with maps and public datasets, to help people understand, navigate or look after the world around them.

**Judging criteria:**
- **Real-world usefulness:** a clear user and a problem that exists outside the hackathon.
- **How well it reads the world:** accuracy, and sensible handling of messy inputs (blurry photos, background noise, gaps in the data).
- **Technical execution:** a working end-to-end demo that combines AI models with real data.
- **Responsibility:** respect for privacy (faces, number plates, people's locations), and honesty about uncertainty when the model isn't sure.

**Example ideas (starting points, not tracks):** accessibility guide from photos · "what's happening near me" explorer · trip/hike conditions planner · wildlife and plant ID · noise/air-quality map · report-it-from-a-photo (potholes, fly-tipping) · sound-aware assistant · label/menu allergen reader · flood and heat risk checker.

**Suggested data sources:** OpenStreetMap · TfL Unified API · London Datastore · Met Office · Open-Meteo · Breathe London · London Air Quality Network · Environment Agency flood-monitoring API · iNaturalist · GBIF. Any other public data, your own photos/recordings, or phone sensors/cameras are also allowed.

**Where CoFoundr already touches it (for free points, no extra build):**
- Invoice photos → structured data, including handling blurry photos
- Voice order entry (audio → orders) with background café noise
- Weather (Open-Meteo) and events feeding the forecast; events heatmap on a map
- Barista and owner confirmation steps, plus visible uncertainty and confidence = the "Responsibility" criterion

---

## Sponsor bounties

### MongoDB — "Build Anything, Change Everything with MongoDB Atlas"
- **Prize:** £6,800 cash (the official page says "to be announced"). Each builder can claim a $100 Atlas credit at the MongoDB booth (ground floor, Encode Hub).
- **Challenge:** an innovative app that solves a real-world problem, with **MongoDB Atlas as its foundational backend data store**.
- **Requirements:**
  - The app must rely on Atlas for its **core functionality**.
  - Use **at least one** of the scored Atlas features. **The more you use, the more points you get.**
- **Scored Atlas features (official list):**
  1. Atlas Search (text search)
  2. Atlas Vector Search
  3. Hybrid Search
  4. Automated Embedding
  5. Atlas Stream Processing
  6. Voyage AI by MongoDB models (some are already part of Automated Embedding)
- **Languages:** Python is allowed.
- **Submission:** a **GitHub repository** + a **3-minute demo video** showing the app in action **and MongoDB in use**.

**CoFoundr plan — use as many scored features as fit naturally:**
- [x] Atlas as the single source of truth (all collections)
- [x] **Atlas Search** — full-text search over invoices/suppliers (`backend/db/search.py`, index `invoices_text`, typo-tolerant; used by the price check). *Dashboard search box not built yet*
- [x] **Vector Search** — find similar past invoices/suppliers (`similar_invoices`, index `invoices_semantic`; powers the cross-supplier price check). *Events matching not built*
- [x] **Hybrid Search** — combine text + vector search with `$rankFusion` (`search.hybrid`) (e.g. "invoices from the milk supplier" by keyword + meaning)
- [x] **Automated Embedding** — Atlas embeds invoices' `search_text` on insert (`autoEmbed`), no embedding code of ours
- [x] **Voyage AI** — `voyage-4` behind Automated Embedding
- [ ] **Atlas Stream Processing** — trigger the agent when a new invoice/order lands (replaces plain change streams, which aren't on the scored list)
- [x] Time-series collection for POS sales (nice, but **not on the scored list**)
- [ ] Demo video + README name each Atlas feature explicitly and show it working

### Google DeepMind — "Build with Gemini 4 Argon"
- **Prize:** $15,000 in Gemini API credits.
- **Rule:** a working app or autonomous agent that **solves a problem for a real person**.
- **Must use the Gemini 4 Argon model:** model ID `gemini-4-argon`.
- **Format:** judges score **live working demos, not slide decks**: a **3-minute live demo** + **2 minutes of Q&A**.

**Judging criteria (weighted):**
| Weight | Criterion | What judges ask |
|---|---|---|
| **50%** | Working live demo | Does it run live with real inputs in under 3 min? Did the team use `gemini-4-argon`, Managed Agents or companion models effectively, **with a real verification loop**? |
| **25%** | Problem and usefulness | Does it solve a concrete problem for a **specific person you can name**? Could it grow into a product or open-source tool after the hackathon? |
| **15%** | Originality / needs Gemini 4 Argon | Is it only possible because of Argon's 1M unified context, long decode, frontier reasoning, agentic video analysis, 3D/WebGPU generation or multi-agent orchestration? Does it **keep people in control** of actions that affect external systems, money or private data? |
| **10%** | Clarity of pitch | Did the team show who the user is and demo the working product clearly in 3 minutes, **without slides**? |

**Avoid (disqualified or low-scoring):**
- A basic chat wrapper, or a simple Q&A bot over a single PDF
- A static page with no autonomous reasoning, verification loop or multimodal workflow
- Unverified medical, mental health or **financial advice** bots without human-in-the-loop safeguards → our approval flow is what covers this
- Anything built before kickoff. Open-source libraries and starter templates are OK, but the demo must only show code written during the hackathon

**CoFoundr plan:**
- [x] Use `gemini-4-argon` for all reasoning calls (`GEMINI_MODEL`, `fly.toml`)
- [x] **Verification loop:** Gemini checks its own extraction (e.g. line items sum to the total, due date is valid) and flags low confidence for human review. Also: forecast adjustments checked (factors exist, add up, direction), board memo claims recomputed from MongoDB, fee summary checked against its own worked example
- [x] Use Argon's **1M context**: the board meeting (`backend/ai/board.py`) feeds every till line, invoice, approval, forecast and agent action in one call (~169k tokens live)
- [ ] Consider **multi-agent orchestration** (e.g. invoice agent, forecast agent, planner agent) or Managed Agents if time allows
- [ ] Named user: the GAIA Coffee owner
- [ ] Demo rehearsed to run live in **under 3 minutes, no slides**
- [x] Multimodal: invoice photo/PDF → structured JSON (+ card fee summary image → rates)
- [x] Reasoning shown in UI: forecast adjustment explanations, approval reasons, board memo with every number checked, forecast lessons
- [ ] Function calling over MongoDB for prioritisation
- [ ] "Real person" story: GAIA Coffee owner (synthetic data, real business)

### Saint — "Best Use of Saint"
- **Prize:** £200 cash, a Saint for each team member, and 3 months of Saint free.
- **Rule:** put Saint to work on a **real problem** over the weekend.

**What they're looking for:**
- **Cool factor:** would people crowd round to watch it?
- **Working proof:** Saint actually doing the job in your demo, live or recorded, **with the before and the after**.
- **Real-world use:** would this still be running on Monday?
- **Human control:** what Saint does on its own, and where it stops and asks first.

**CoFoundr plan:**
- [ ] Demo shows a clear **before/after** (e.g. pile of unpaid invoices → chased + approved via WhatsApp)
- [ ] Be explicit about the split: Saint drafts and briefs **on its own**, and **stops and asks** before any money action
- [ ] Saint is the owner's WhatsApp interface: send invoice photos in, receive morning brief out
- [ ] Approvals by WhatsApp reply ("approve") update MongoDB
- [ ] Saint reads CoFoundr's `/brief` endpoint
- [ ] Show Saint doing real work in the demo, not a bolt-on

---

### Treg (treg.to) — perk, not a bounty
- $20 of Treg credits per builder for the weekend.
- **Overall top three projects also win Treg credits** (on top of the partner bounties): 1st $500 · 2nd $250 · 3rd $100.
- Treg = "OpenRouter for agent tools": one MCP server + one key → 4,000+ endpoints from 114 providers, pay per call. The agent asks for a task; Treg shows price, success rate and speed per option.

**CoFoundr plan:**
- [ ] Give the Gemini agent Treg as its tool gateway (one key, many tools)
- [ ] Partnership finder: local business data + company lookup (Diffbot/Crunchbase) for event organisers and nearby businesses
- [ ] Outreach: find/verify contact emails (Hunter) before drafting partnership messages
- [ ] Invoice chaser: verify customer email deliverability before sending a reminder
- [ ] Events: check the Treg catalog (incl. Apify actors) for an events source
- [ ] Log every Treg call (tool, cost, result) in MongoDB → shows agent autonomy + cost awareness

---

## How judging works (confirmed with team)
- **Main judging = the two tracks:** Agents That Ship and The Real World. Sponsor bounties are technology use cases.
- **Weighting:** how many features you use **while highlighting each one's main strength**. Breadth + depth: every tool must do the job it's best at, visibly.
- **Enter everything:** one project can go on all tracks and all bounties. Submit to all of them.

### What this means for CoFoundr
| Tech | Its main strength to highlight |
|---|---|
| MongoDB Atlas | Flexible document store + Vector Search / Atlas Search / Voyage AI / time-series / change streams |
| Gemini 4 Argon | Multimodal understanding + deep reasoning with visible explanations |
| Saint | A real AI employee the owner talks to on WhatsApp |
| Treg | One gateway to thousands of real-world data tools, chosen by the agent |

## To confirm
- [x] Submission format: GitHub repo + 3-min demo video (MongoDB); 3-min live demo + 2-min Q&A, no slides (Gemini)
- [ ] Main hackathon submission format (beyond the sponsor requirements above)
- [x] Gemini model ID: `gemini-4-argon`
- [x] Gemini 4 Argon API access / keys
- [ ] Saint judging specifics — ask the Saint team
