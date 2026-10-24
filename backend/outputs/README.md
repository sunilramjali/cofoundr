# backend/outputs: what the owner sees and receives
**Owner: Jade**

- `brief.py`: the WhatsApp-ready brief (`/brief`)
- `updates.py`: automatic WhatsApp messages (`/updates`) built from the activity log
- `email.py`: sends a reminder email **only after the owner approves** (called by `approvals.decide`); simulated until MVP2

Read data via `backend/db/schema.py` names. Never change data here; decisions go through `backend/approvals.py`.
