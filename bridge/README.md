# bridge: the CoFoundr web app (FastAPI on Fly.io)
**Owner: Jade**

One app serving:
- **Dashboard UI** for the owner (server-rendered HTML in `templates/`)
- **WhatsApp** via Twilio: `POST /whatsapp/twilio` (signed webhook in) + replies and `/updates` pushed out
- **Token-protected read-only feeds**: `GET /brief`, `GET /updates`; and `GET /health`

Routes stay thin: inputs call `backend.pipeline`, decisions call `backend.approvals`, output text comes from `backend.outputs`.

## Local development: use the sandbox
`.env` points at the shared Atlas demo database, so a normal local server changes the live demo's data when you click
Approve, Pay now or Check inbox. Run locally in **sandbox mode** instead:
```
COFOUNDR_SANDBOX=1 uvicorn bridge.main:app --reload --port 8000
```
It copies the demo data into memory once (read-only from Atlas); everything you do locally stays local. Inbox checks and
email sending are off; the sidebar shows "Sandbox · local copy". See `tools/sandbox.py`.

## Security
Every POST checks the request came from CoFoundr's own pages (Origin/Referer must match the host), so another website
can't approve, pay or upload for the owner. Twilio's signed webhook calls (no Origin) still work.
