# bridge: the CoFoundr web app (FastAPI on Fly.io)
**Owner: Sunil**

One app serving:
- **Dashboard pages** for the owner (server-rendered HTML in `templates/`): upload, invoices, approval queue
- **Saint endpoints**: `GET /brief`, `POST /approve`, `GET /health`

Talks to Gemini (`backend/ai`) and MongoDB (`backend/db`). See project-plan.md §3a.
