"""CoFoundr web app (FastAPI on Fly.io): owner dashboard, WhatsApp (Twilio webhook) and the token-protected read-only feeds.

Run locally (from the repo root):
    uvicorn bridge.main:app --reload --port 8000

Pages are plain server-rendered HTML (no JS needed).
Endpoints are sync `def` so blocking PyMongo / Gemini calls run in FastAPI's thread pool.
"""
import html
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend import approvals, payments
from backend.ai import board, fees, overdue, review
from backend.inputs import evo, gmail, whatsapp as whatsapp_in
from backend.inputs.replies import parse_reply  # noqa: F401 (also imported from here by tests)
from backend.outputs import assistant, whatsapp as whatsapp_out
from backend.outputs.brief import build_brief
from backend.outputs.updates import get_updates
from backend.pipeline import ingest_invoice
from backend.db import activity, schema
from backend.db.client import RealDataRefused, get_db
from bridge import view

if os.environ.get("COFOUNDR_SANDBOX"):  # local development on an in-memory copy of the demo data (tools/sandbox.py)
    from tools import sandbox
    sandbox.install()



@asynccontextmanager
async def lifespan(_app):
    """GMAIL_POLL_SECONDS set (fly.toml) → check the demo inbox in the background; unset (local, tests) → only on click.
    WHATSAPP_PUSH_SECONDS set → push new /updates to the owner's WhatsApp in the background."""
    every = int(os.environ.get("GMAIL_POLL_SECONDS") or 0)
    stop = gmail.start_polling(every) if every > 0 else None
    push = int(os.environ.get("WHATSAPP_PUSH_SECONDS") or 0)  # same idea: on Fly only, never from a laptop
    stop_push = whatsapp_out.start_polling(push) if push > 0 and whatsapp_out.configured() else None
    yield
    for s in (stop, stop_push):
        if s:
            s.set()


app = FastAPI(title="CoFoundr", lifespan=lifespan)


@app.middleware("http")
async def same_site_posts_only(request: Request, call_next):
    """Block cross-site form posts (CSRF): another website must never be able to approve, pay or upload for the owner.
    Browsers send Origin (or at least Referer) on cross-site POSTs; Twilio's server-to-server webhook calls send neither."""
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


def _public_url(request: Request) -> str:
    return os.environ.get("PUBLIC_URL") or str(request.base_url).rstrip("/")


PAGES = {"/", "/board", "/approvals", "/sales", "/invoices", "/inbox", "/activity", "/whatsapp", "/inventory", "/settings"}


def _back(msg: str, to: Optional[str] = "/") -> RedirectResponse:
    """Back to the page the owner acted on (only our own pages: never an open redirect)."""
    return RedirectResponse(f"{to if to in PAGES else '/'}?msg={quote(msg)}", status_code=303)


# ---------- Owner dashboard: a home of modules, each opening into its own page ----------

NAV = [  # sidebar: (label, path, icon); pages not listed here are reached from the home feed
    ("Home", "/", "home"), ("Board", "/board", "bulb"), ("Insights", "/sales", "chart"), ("Finances", "/invoices", "pound"), ("Inventory", "/inventory", "box"),
    ("Ask", "/whatsapp", "chat"), ("Tasks", "/approvals", "yes"),
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
        "memo": db[schema.BOARD_MEMOS].find_one({"business_id": BUSINESS_ID}, sort=[("generated_at", -1)]),
        "memo_running": _board_running.locked(),
        "record": _forecast_record(db),
        "fee_audit": db[schema.FEE_AUDITS].find_one({"business_id": BUSINESS_ID}, sort=[("created_at", -1)]),
        "checks": view.payment_checks(pending, invoices),
        "sandbox": bool(os.environ.get("COFOUNDR_SANDBOX")),
        "inbox": {"configured": gmail.configured(), "address": os.environ.get("GMAIL_ADDRESS", ""),
                  "polling": int(os.environ.get("GMAIL_POLL_SECONDS") or 0), "last": gmail.last_check},
    }


def _forecast_record(db) -> dict:
    """How the forecasts did against actual takings (backend/ai/review.py), for the Sales page."""
    reviews = list(db[schema.FORECAST_REVIEWS].find({"business_id": BUSINESS_ID}).sort("date", -1).limit(30))
    cap, why = review.earned_cap(db)
    return {"overall": review.stats(reviews).get("overall"), "days": reviews[:8], "cap": cap, "why": why,
            "full_cap": cap >= review.forecast.MAX_PCT,
            "lessons": list(db[schema.LESSONS].find({"business_id": BUSINESS_ID, "active": True}).sort("created_at", -1))}


def _page(name: str):
    def render(request: Request, msg: Optional[str] = None, show: Optional[str] = None):
        ctx = _context(request, msg)
        ctx.update(path=request.url.path, show=show)
        return templates.TemplateResponse(request, f"{name}.html", ctx)
    return render


for _path, _name in [("/", "home"), ("/board", "board"), ("/approvals", "approvals"), ("/sales", "sales"), ("/invoices", "invoices"),
                     ("/inbox", "inbox"), ("/activity", "activity"), ("/whatsapp", "whatsapp"), ("/inventory", "inventory"),
                     ("/settings", "settings")]:
    app.add_api_route(_path, _page(_name), methods=["GET"], response_class=HTMLResponse, name=f"page_{_name}")


@app.post("/upload")
def upload(file: UploadFile = File(...), back: str = Form("/inbox")):
    data = file.file.read(MAX_UPLOAD + 1)
    mime = file.content_type or "application/octet-stream"
    result = ingest_invoice(data, mime, "pdf" if mime == "application/pdf" else "manual", origin="dashboard upload")
    return _back(result.message, back)


@app.post("/import/evo")
def import_evo(file: UploadFile = File(...), back: str = Form("/sales")):
    """EVO's transactions CSV, uploaded by hand (EVO has no API). Card data is never stored (backend.inputs.evo).
    On the shared demo database only the synthetic sample is accepted; real exports are refused, nothing stored."""
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return _back("That file is too big for an EVO export (10 MB max).", back)
    try:
        parsed = evo.parse(data)
        result = payments.record_payments(parsed.payments, origin="EVO CSV (dashboard upload)", synthetic=parsed.synthetic)
    except RealDataRefused:
        return _back("That's a real EVO export. This demo only takes synthetic data, so nothing was stored. "
                     "Import real data on your own machine into your own database.", back)
    except ValueError as e:
        return _back(str(e), back)
    return _back(result.message + (f" {len(parsed.errors)} row(s) couldn't be read." if parsed.errors else ""), back)


@app.post("/import/fees")
def import_fees(file: UploadFile = File(...), back: str = Form("/sales")):
    """The card provider's fee summary (photo or PDF): Gemini reads it, its own worked examples check the reading,
    then every stored card payment is audited against it. Nothing is claimed from the provider here."""
    data = file.file.read(MAX_UPLOAD + 1)
    mime = file.content_type or "application/octet-stream"
    if len(data) > MAX_UPLOAD or not (mime.startswith("image/") or mime == "application/pdf"):
        return _back("Upload the fee summary as a photo or PDF (10 MB max).", back)
    if _too_soon("fees", FEES_EVERY):
        return _back("I'm still on the last fee summary. Try again in a minute.", back)
    try:
        doc = fees.run(data, mime)
    except RealDataRefused as e:
        return _back(str(e), back)
    except Exception as e:  # Gemini busy after retries, unreadable file: say so, store nothing
        return _back(f"I couldn't read that fee summary ({getattr(e, 'code', None) or type(e).__name__}). Try again in a minute.", back)
    if doc["problems"]:
        return _back("I read the fee summary but its numbers didn't check out against its own example, so I didn't audit "
                     "anything. Please check it.", back)
    a = doc["audit"]
    return _back(f"Checked {a['checked']} card payments: {a['matched']} charged correctly, {len(a['overcharged'])} overcharged "
                 f"(£{a['extra_total']:.2f}).", back)


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


@app.post("/ask")
def ask(text: str = Form(""), back: str = Form("/")):
    """The chat bar. Decisions ("approve 1", "reject 2") follow exactly the WhatsApp rules and never reach Gemini;
    anything else is a question for the Gemini assistant, which can read the data but not change it."""
    text = text.strip()
    if not text:
        return _back("Type a question first, e.g. “what's waiting?”.", back)
    parsed = parse_reply(text)
    if not parsed:
        return _back(assistant.answer(text).text, back)
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


_board_running = threading.Lock()
BOARD_EVERY = 600  # seconds between board meetings: each one reads ~170k tokens, so a public button must not be spammable
FEES_EVERY = 60  # seconds between fee summary uploads (one Gemini read each)
_last_run: dict = {}  # what → time.monotonic() of its last run; absent = never (monotonic starts near 0 after a reboot)


def _too_soon(what: str, every: int) -> Optional[int]:
    """Seconds left before `what` may run again, or None (and the clock restarts now)."""
    left = int(_last_run[what] + every - time.monotonic()) if what in _last_run else 0
    if left > 0:
        return left
    _last_run[what] = time.monotonic()
    return None


@app.post("/board/run")
def run_board(back: str = Form("/board")):
    """Gemini reads everything (about a minute), so it runs in the background; one at a time, at most every 10 minutes."""
    if _board_running.locked():
        return _back("A board meeting is already running. Refresh in a minute.", back)
    left = _too_soon("board", BOARD_EVERY)
    if left:
        return _back(f"The last board meeting was just now. The next one can start in {left // 60 + 1} minutes.", back)
    if not _board_running.acquire(blocking=False):
        return _back("A board meeting is already running. Refresh in a minute.", back)

    def work():
        try:
            board.run()
        finally:
            _board_running.release()
    threading.Thread(target=work, daemon=True).start()
    return _back("Gemini is reading everything the business has recorded (about a minute). Refresh to see the memo.", back)


@app.post("/run-check")
def run_check(back: str = Form("/approvals")):
    drafted = overdue.run()
    return _back(f"Overdue check done: {len(drafted)} new action(s) drafted." if drafted else "Overdue check done: nothing new.", back)


# ---------- token-protected feeds (read-only) ----------

@app.get("/brief")
def brief(request: Request, token: Optional[str] = Query(None), format: str = "text",
          x_token: Optional[str] = Header(None)):
    _check_token(token or x_token)
    data = build_brief(_public_url(request))
    activity.log("brief_served", f"Brief served ({format}): {len(data['pending_approvals'])} pending approval(s)", actor="agent")
    if format == "json":
        return JSONResponse(data)
    if format == "html":  # text includes names read from invoices (untrusted): escape it
        return HTMLResponse(f"<!doctype html><html><body><pre>{html.escape(data['text'])}</pre></body></html>")
    return PlainTextResponse(data["text"])


@app.get("/updates")
def updates(token: Optional[str] = Query(None), x_token: Optional[str] = Header(None),
            since: Optional[str] = None, format: str = "json", peek: bool = False):
    """What the owner should hear about, unprompted. The WhatsApp pusher sends each message (backend/outputs/whatsapp.py).
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


def _reply_on_whatsapp(params: dict) -> None:
    """After Twilio got its answer: handle the owner's message (Gemini may take a while) and send the reply."""
    try:
        reply = whatsapp_in.handle(params)
    except Exception as e:  # never silent: the owner hears something went wrong
        activity.log("whatsapp_failed", f"Couldn't handle a WhatsApp message ({type(e).__name__})", error=type(e).__name__)
        reply = "Sorry, something went wrong on my side and nothing was changed. Please try again."
    if reply:
        sent = whatsapp_out.send(reply)
        if not sent.ok:
            activity.log("whatsapp_send_failed", f"Couldn't send a WhatsApp reply: {sent.error}", error=sent.error)


@app.post("/whatsapp/twilio")
async def twilio_webhook(request: Request, background: BackgroundTasks):
    """Twilio delivers each WhatsApp message here. Signed by Twilio, so nobody else can post as the owner."""
    if not whatsapp_out.configured():
        raise HTTPException(503, "WhatsApp is off.")
    params = [(k, str(v)) for k, v in (await request.form()).multi_items()]
    url = (os.environ.get("PUBLIC_URL") or str(request.base_url).rstrip("/")) + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    if not whatsapp_in.signature_ok(url, params, request.headers.get("X-Twilio-Signature"), whatsapp_out._config()[1]):
        activity.log("whatsapp_rejected", "Rejected a WhatsApp webhook call with a bad Twilio signature", actor="agent")
        raise HTTPException(403, "Bad signature.")
    background.add_task(_reply_on_whatsapp, dict(params))
    return Response("<Response/>", media_type="text/xml")  # empty TwiML: the reply is sent separately


@app.get("/health")
def health():
    return {"ok": True}
