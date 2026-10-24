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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend import approvals
from backend.ai import overdue
from backend.inputs import gmail
from backend.outputs.brief import build_brief
from backend.outputs.updates import get_updates
from backend.pipeline import ingest_invoice
from backend.db import activity, schema
from backend.db.client import get_db
from bridge import view

if os.environ.get("COFOUNDR_SANDBOX"):  # local development on an in-memory copy of the demo data (tools/sandbox.py)
    from tools import sandbox
    sandbox.install()



@asynccontextmanager
async def lifespan(_app):
    """GMAIL_POLL_SECONDS set (fly.toml) → check the demo inbox in the background; unset (local, tests) → only on click."""
    every = int(os.environ.get("GMAIL_POLL_SECONDS") or 0)
    stop = gmail.start_polling(every) if every > 0 else None
    yield
    if stop:
        stop.set()


app = FastAPI(title="CoFoundr", lifespan=lifespan)


@app.middleware("http")
async def same_site_posts_only(request: Request, call_next):
    """Block cross-site form posts (CSRF): another website must never be able to approve, pay or upload for the owner.
    Browsers send Origin (or at least Referer) on cross-site POSTs; Saint's server-to-server calls send neither."""
    if request.method == "POST":
        source = request.headers.get("origin") or request.headers.get("referer")
        host = request.headers.get("host", "")
        if source is not None and (source == "null" or urlsplit(source).netloc != host):
            return PlainTextResponse("Blocked: this action can only be sent from CoFoundr's own pages.", status_code=403)
    return await call_next(request)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = lambda v: f"£{v:,.2f}" if v is not None else "–"
templates.env.filters["day"] = lambda d: d.strftime("%d %b") if d else "–"
templates.env.filters["stamp"] = lambda d: d.strftime("%d %b %H:%M") if d else "–"
templates.env.filters["money0"] = view.money0
templates.env.filters["ago"] = view.ago
templates.env.globals["due_label"] = view.due_label

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


PAGES = {"/", "/approvals", "/sales", "/invoices", "/inbox", "/activity", "/whatsapp", "/inventory", "/settings"}


def _back(msg: str, to: Optional[str] = "/") -> RedirectResponse:
    """Back to the page the owner acted on (only our own pages: never an open redirect)."""
    return RedirectResponse(f"{to if to in PAGES else '/'}?msg={quote(msg)}", status_code=303)


# ---------- Owner dashboard: a home of modules, each opening into its own page ----------

NAV = [  # sidebar: (label, path, icon); pages not listed here are reached from the home feed
    ("Home", "/", "home"), ("Insights", "/sales", "chart"), ("Finances", "/invoices", "pound"), ("Inventory", "/inventory", "box"),
    ("Saint", "/whatsapp", "chat"), ("Tasks", "/approvals", "yes"),
]


def _context(request: Request, msg: Optional[str]) -> dict:
    db = get_db()
    now = view.london_now()
    invoices = list(db[schema.INVOICES].find({"business_id": BUSINESS_ID}).sort([("status", -1), ("due_date", 1)]))
    pending = approvals.pending()
    review = [i for i in invoices if i.get("needs_review") and i["status"] != "paid"]
    brief = build_brief(_public_url(request))
    forecasts = list(db[schema.FORECASTS].find({"business_id": BUSINESS_ID, "date": {"$gte": now.date().isoformat()}})
                     .sort("date", 1).limit(7))
    since = view.days_ago(now, 29)
    orders = list(db[schema.ORDERS].find({"business_id": BUSINESS_ID, "placed_at": {"$gte": since}}, {"placed_at": 1, "total": 1}))
    week = view.week(forecasts, view.weather_by_date(), now.date())
    sales = view.sales_history(orders, now.date())
    gmail_logs = list(db[schema.AGENT_LOG].find({"business_id": BUSINESS_ID, "kind": "gmail_checked"}).sort("at", -1).limit(20))
    return {
        "msg": msg,
        "now": now,
        "greeting": view.greeting(now),
        "headline": view.headline(pending, brief["overdue_receivable_total"], review),
        "pending": pending,
        "tones": view.TONES,
        "invoices": invoices,
        "review": review,
        "brief": brief,
        "timeline": view.timeline(activity.recent(30), now),
        "week": week,
        "sales": sales,
        "trend": view.trend(sales, week),
        "stock": view.stock_estimate(list(db[schema.STOCK_COUNTS].find({"business_id": BUSINESS_ID})), now),
        "opportunities": view.opportunities(gmail_logs),
        "nav": NAV,
        "sandbox": bool(os.environ.get("COFOUNDR_SANDBOX")),
        "inbox": {"configured": gmail.configured(), "address": os.environ.get("GMAIL_ADDRESS", ""),
                  "polling": int(os.environ.get("GMAIL_POLL_SECONDS") or 0), "last": gmail.last_check},
    }


def _page(name: str):
    def render(request: Request, msg: Optional[str] = None, show: Optional[str] = None):
        ctx = _context(request, msg)
        ctx.update(path=request.url.path, show=show)
        return templates.TemplateResponse(request, f"{name}.html", ctx)
    return render


for _path, _name in [("/", "home"), ("/approvals", "approvals"), ("/sales", "sales"), ("/invoices", "invoices"),
                     ("/inbox", "inbox"), ("/activity", "activity"), ("/whatsapp", "whatsapp"), ("/inventory", "inventory"),
                     ("/settings", "settings")]:
    app.add_api_route(_path, _page(_name), methods=["GET"], response_class=HTMLResponse, name=f"page_{_name}")


@app.post("/upload")
def upload(file: UploadFile = File(...), back: str = Form("/inbox")):
    data = file.file.read(MAX_UPLOAD + 1)
    mime = file.content_type or "application/octet-stream"
    result = ingest_invoice(data, mime, "pdf" if mime == "application/pdf" else "manual", origin="dashboard upload")
    return _back(result.message, back)


@app.post("/approvals/{approval_id}/decide")
def decide_from_dashboard(approval_id: str, decision: str = Form(...), draft: Optional[str] = Form(None),
                          back: str = Form("/approvals")):
    approval = approvals.find(approval_id=approval_id)
    if not approval:
        return _back("Approval not found.", back)
    try:
        result = approvals.decide(approval, decision, via="dashboard", edited_draft=draft)
    except approvals.DecisionError as e:
        return _back(str(e), back)
    return _back(f"#{result['ref']} {result['status']}: {result['effect']}.", back)


@app.post("/saint")
def message_saint(text: str = Form(""), back: str = Form("/")):
    """The chat bar. Decisions ("approve 1", "reject 2") follow exactly the WhatsApp rules; anything else waits for Saint."""
    text = text.strip()
    if not text:
        return _back("Type a message for Saint first.", back)
    parsed = parse_reply(text)
    if not parsed:
        return _back('Saint isn\'t connected to the chat bar yet. Decisions work now: "approve 1" or "reject 1".', back)
    decision, ref = parsed
    approval = approvals.find(ref=ref)
    if not approval:
        return _back(f"There's no #{ref} waiting for you.", back)
    try:
        result = approvals.decide(approval, decision, via="dashboard")
    except approvals.DecisionError as e:
        return _back(str(e), back)
    return _back(f"#{result['ref']} {result['status']}: {result['effect']}.", back)


@app.post("/check-inbox")
def check_inbox(back: str = Form("/inbox")):
    return _back(gmail.check_inbox().message, back)


@app.post("/run-check")
def run_check(back: str = Form("/approvals")):
    drafted = overdue.run()
    return _back(f"Overdue check done: {len(drafted)} new action(s) drafted." if drafted else "Overdue check done: nothing new.", back)


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
