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
| **Revenue forecaster** | Baseline forecast from sales history, adjusted by Gemini for local events and weather, with a plain-English reason ("+28% Sat: market nearby + sunny") |
| **Approval flow** | Every finance action waits in an Approve / Edit / Reject queue, in the dashboard or by replying on WhatsApp |
| **Auto-prioritise & schedule** | Ranks open tasks by impact, with a one-line reason each, and builds a weekly plan |
| **Morning brief** | Owner asks on WhatsApp, "what's my day?", and gets the top priorities, forecast and pending approvals |

**Stack:** MongoDB Atlas (single source of truth + search) · Gemini 4 Argon (multimodal extraction + reasoning) · Saint (WhatsApp AI employee) · Treg (tool gateway) · FastAPI web app on Fly.io (dashboard + Saint bridge)

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
| Atlas Search | _TBD_ | |
| Atlas Vector Search | _TBD_ | |
| Hybrid Search | _TBD_ | |
| Automated Embedding | _TBD_ | |
| Atlas Stream Processing | _TBD_ | |
| Voyage AI | _TBD_ | |

### Gemini 4 Argon
- **Working live demo (50%):** _TBD_
- **Problem and usefulness (25%):** _TBD_
- **Originality / needs Argon (15%):** _TBD_
- **Clarity of pitch (10%):** _TBD_

### Saint
- **Cool factor:** _TBD_
- **Working proof (before → after):** _TBD_
- **Real-world use:** _TBD_
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
```bash
git clone <repo-url> && cd encode-hackathon
cp .env.example .env        # fill in your keys
pip install -r requirements.txt
uvicorn bridge.main:app --reload   # dashboard + Saint endpoints at http://localhost:8000 (TBD)
```

---

## Team
- **Sunil**
- **Ceyda**
