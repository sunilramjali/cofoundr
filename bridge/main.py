"""CoFoundr web app (FastAPI on Fly.io): owner dashboard + Saint endpoints.

Run locally (from the repo root):
    uvicorn bridge.main:app --reload --port 8000

Pages are plain server-rendered HTML (no JS), so Saint can read them too.
Endpoints are sync `def` so blocking PyMongo / Gemini calls run in FastAPI's thread pool.
"""
import html
import os
import re
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from backend import approvals
from backend.ai import overdue
from backend.outputs.brief import build_brief
from backend.outputs.updates import get_updates
from backend.pipeline import ingest_invoice
from backend.db import activity, schema
from backend.db.client import get_db

app = FastAPI(title="CoFoundr")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = lambda v: f"£{v:,.2f}" if v is not None else "–"
templates.env.filters["day"] = lambda d: d.strftime("%d %b") if d else "–"
templates.env.filters["stamp"] = lambda d: d.strftime("%d %b %H:%M") if d else "–"

BUSINESS_ID = "gaia-coffee"
MAX_UPLOAD = 10 * 1024 * 1024  # pipeline.ingest_invoice rejects anything larger


def _check_token(token: Optional[str]) -> None:
    expected = os.environ.get("BRIEF_TOKEN", "")
    # Compare bytes: compare_digest raises on non-ASCII str, which would be a 500 instead of a 403
    if not expected or not token or not secrets.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")


# The whole reply must be one command: "approve 3", "Yes #3", "reject 2!". Anything else ("don't approve 3",
# "approve 1 and 2", "approve the £450 one") is refused, so a negated or ambiguous message never moves money.
REPLY = re.compile(r"^\s*(approve|approved|yes|ok|reject|rejected|no)\s*#?\s*(\d{1,6})\s*[.!]*\s*$", re.IGNORECASE)


def parse_reply(text: str) -> Optional[tuple]:
    m = REPLY.match(text or "")
    if not m:
        return None
    return ("reject" if m.group(1).lower() in ("reject", "rejected", "no") else "approve"), int(m.group(2))


def _public_url(request: Request) -> str:
    return os.environ.get("PUBLIC_URL") or str(request.base_url).rstrip("/")


def _back(msg: str) -> RedirectResponse:
    return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)


# ---------- Owner dashboard ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, msg: Optional[str] = None):
    db = get_db()
    invoices = list(db[schema.INVOICES].find({"business_id": BUSINESS_ID}).sort([("status", -1), ("due_date", 1)]))
    return templates.TemplateResponse(request, "index.html", {
        "msg": msg,
        "pending": approvals.pending(),
        "decided": approvals.recent_decided(),
        "invoices": invoices,
        "review": [i for i in invoices if i.get("needs_review") and i["status"] != "paid"],
        "brief": build_brief(_public_url(request)),
        "activity": activity.recent(),
    })


@app.post("/upload")
def upload(file: UploadFile = File(...)):
    data = file.file.read(MAX_UPLOAD + 1)
    mime = file.content_type or "application/octet-stream"
    result = ingest_invoice(data, mime, "pdf" if mime == "application/pdf" else "manual", origin="dashboard upload")
    return _back(result.message)


@app.post("/approvals/{approval_id}/decide")
def decide_from_dashboard(approval_id: str, decision: str = Form(...), draft: Optional[str] = Form(None)):
    approval = approvals.find(approval_id=approval_id)
    if not approval:
        return _back("Approval not found.")
    try:
        result = approvals.decide(approval, decision, via="dashboard", edited_draft=draft)
    except approvals.DecisionError as e:
        return _back(str(e))
    return _back(f"#{result['ref']} {result['status']}: {result['effect']}.")


@app.post("/run-check")
def run_check():
    drafted = overdue.run()
    return _back(f"Overdue check done: {len(drafted)} new action(s) drafted." if drafted else "Overdue check done: nothing new.")


# ---------- Saint endpoints ----------

@app.get("/brief")
def brief(request: Request, token: Optional[str] = Query(None), format: str = "text",
          x_token: Optional[str] = Header(None)):
    _check_token(token or x_token)
    data = build_brief(_public_url(request))
    activity.log("brief_served", f"Brief served ({format}): {len(data['pending_approvals'])} pending approval(s)", actor="saint")
    if format == "json":
        return JSONResponse(data)
    if format == "html":  # text includes names read from invoices (untrusted): escape it
        return HTMLResponse(f"<!doctype html><html><body><pre>{html.escape(data['text'])}</pre></body></html>")
    return PlainTextResponse(data["text"])


@app.get("/updates")
def updates(token: Optional[str] = Query(None), x_token: Optional[str] = Header(None),
            since: Optional[str] = None, format: str = "json", peek: bool = False):
    """What the owner should hear about, unprompted. Saint polls this and sends each message on WhatsApp.
    No `since` → the server remembers what was delivered (advances unless peek=true).
    format=text → just the messages, or an empty body when there's nothing to send."""
    _check_token(token or x_token)
    try:
        data = get_updates(since=since, peek=peek)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if format == "text":
        return PlainTextResponse(data["text"])
    return JSONResponse(data)


@app.post("/approve")
def approve(payload: dict = Body(...), token: Optional[str] = Query(None), x_token: Optional[str] = Header(None)):
    """Saint relays an owner's WhatsApp reply.
    Accepts {"ref": 1, "decision": "approve"} or {"text": "approve 1"} (also "reject 2", "yes 1")."""
    _check_token(token or x_token)
    ref, decision = payload.get("ref"), payload.get("decision")
    if "text" in payload:
        parsed = parse_reply(str(payload["text"]))
        if not parsed:
            raise HTTPException(400, 'Couldn\'t understand. Reply with just "approve 1" or "reject 1".')
        decision, ref = parsed
    if decision not in ("approve", "reject") or isinstance(ref, bool) or not str(ref).isdigit():
        raise HTTPException(400, 'Send {"ref": 1, "decision": "approve" | "reject"} or {"text": "approve 1"}.')
    approval = approvals.find(ref=int(ref))
    if not approval:
        raise HTTPException(404, f"No action #{ref}.")
    try:
        result = approvals.decide(approval, decision, via="whatsapp")
    except approvals.DecisionError as e:
        raise HTTPException(409, str(e))
    emoji = "✅" if result["status"] == "approved" else "❌"
    return {"ok": True, "ref": result["ref"], "status": result["status"], "effect": result["effect"],
            "reply": f"{emoji} #{result['ref']} {result['status']}: {result['title']} ({result['effect']})"}


@app.get("/health")
def health():
    return {"ok": True}
