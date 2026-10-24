# bridge: the CoFoundr web app (FastAPI on Fly.io)
**Owner: Jade**

One app serving:
- **Dashboard UI** for the owner (server-rendered HTML in `templates/`)
- **Saint endpoints**: `GET /brief`, `GET /updates`, `POST /approve`, `GET /health`

Routes stay thin: inputs call `backend.pipeline`, decisions call `backend.approvals`, output text comes from `backend.outputs`.
