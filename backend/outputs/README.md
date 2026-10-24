# backend/outputs: what the owner sees and receives
**Owner: Jade**

- `brief.py`: the WhatsApp-ready brief (`/brief`)
- `updates.py`: automatic WhatsApp messages (`/updates`) built from the activity log
- `assistant.py`: **Ask CoFoundr** (the dashboard chat bar, `POST /ask`; replaces Saint). Gemini answers the owner's questions with read-only function-calling tools over MongoDB; every £ in the answer is checked against what the tools returned (one retry, then a visible warning). It can't approve, pay or send: exact "approve N" replies are handled before Gemini. Falls back to a plain data answer when Gemini is down
- `whatsapp.py`: WhatsApp out via **Twilio**: `send()` (replies) and `push_updates()` (the `/updates` feed sent to the owner unprompted, every `WHATSAPP_PUSH_SECONDS`; the cursor only moves past what was really sent). WhatsApp only delivers within 24 h of the owner's last message
- `email.py`: sends a reminder email **only after the owner approves** (called by `approvals.decide`); simulated until MVP2

Read data via `backend/db/schema.py` names. Never change data here; decisions go through `backend/approvals.py`.
