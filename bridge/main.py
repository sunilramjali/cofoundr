"""CoFoundr web app (FastAPI on Fly.io): owner dashboard + Saint endpoints.

Run locally (from the repo root):
    uvicorn bridge.main:app --reload --port 8000

Pages are plain server-rendered HTML (no JS), so Saint can read them too.
Endpoints are sync `def` so blocking PyMongo / Gemini calls run in FastAPI's thread pool.
"""
import os
import re
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from google.genai import errors as genai_errors

from backend import approvals
from backend.ai import overdue
from backend.ai.extract import extract_invoice, save_invoice, to_invoice_doc
from backend.brief import build_brief
from backend.updates import get_updates
from backend.db import activity, schema
from backend.db.client import get_db

app = FastAPI(title="CoFoundr")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = lambda v: f"£{v:,.2f}" if v is not None else "–"
templates.env.filters["day"] = lambda d: d.strftime("%d %b") if d else "–"
templates.env.filters["stamp"] = lambda d: d.strftime("%d %b %H:%M") if d else "–"

BUSINESS_ID = "gaia-coffee"
MAX_UPLOAD = 10 * 1024 * 1024


def _check_token(token: Optional[str]) -> None:
    expected = os.environ.get("BRIEF_TOKEN", "")
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing token.")


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
    if not data:
        return _back("No file uploaded.")
    if len(data) > MAX_UPLOAD:
        return _back("File too large (max 10 MB).")
    mime = file.content_type or "application/octet-stream"
    try:
        ex, issues, attempts = extract_invoice(data, mime)
    except genai_errors.APIError as e:
        return _back(f"Gemini couldn't read it right now ({e.code}). Try again in a minute.")
    doc, created = save_invoice(to_invoice_doc(ex, issues, attempts, "pdf" if mime == "application/pdf" else "manual"))
    if not created:
        return _back(f"{doc['invoice_number']} is already in CoFoundr.")
    verdict = "needs your review: " + issues[0] if issues else f"checked ✓ (attempt {attempts})"
    activity.log("invoice_extracted", f"Read {doc['invoice_number']} from {doc['counterparty']}, "
                 f"£{doc['amount']:.2f}, {verdict}", invoice_number=doc["invoice_number"], attempts=attempts)
    drafted = overdue.run()
    extra = f" {len(drafted)} new action(s) drafted for approval." if drafted else ""
    return _back(f"Added {doc['invoice_number']} ({doc['counterparty']}, £{doc['amount']:.2f}): {verdict}.{extra}")


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
    if format == "html":
        return HTMLResponse(f"<!doctype html><html><body><pre>{data['text']}</pre></body></html>")
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
        m = re.search(r"\b(approve|yes|ok|reject|no)\b\D*(\d+)", str(payload["text"]).lower())
        if not m:
            raise HTTPException(400, 'Couldn\'t understand. Reply like "approve 1" or "reject 1".')
        decision = "approve" if m.group(1) in ("approve", "yes", "ok") else "reject"
        ref = int(m.group(2))
    if decision not in ("approve", "reject") or ref is None:
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
