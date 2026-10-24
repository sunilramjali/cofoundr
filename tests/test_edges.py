"""Edges (owner: Jade): web app routes, Saint endpoints, owner-facing messages. Fast, no MongoDB/Gemini needed."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from bridge.main import app


def test_health_and_token_guard():
    client = TestClient(app)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/brief").status_code == 403
    assert client.post("/approve", json={"text": "approve 1"}).status_code == 403
    assert client.get("/updates").status_code == 403


def test_update_messages_are_whatsapp_ready():
    from backend.outputs.updates import approval_message, invoice_message
    ok = invoice_message({"invoice_number": "EDC-1", "counterparty": "Eastside Dairy Co", "amount": 176.7,
                          "due_date": datetime(2026, 11, 7, tzinfo=timezone.utc), "needs_review": False})
    assert "EDC-1" in ok and "£176.70" in ok and "07 Nov" in ok and "✓" in ok
    bad = invoice_message({"invoice_number": "BLR-1", "counterparty": "Bow Lane", "amount": 263.0, "needs_review": True,
                           "verification_issues": ["Line items add up to 237.00 but the printed total is 263.00."]})
    assert bad.startswith("⚠️") and "237.00" in bad
    msg = approval_message({"ref": 4, "title": "Chase Hackney Hub Coworking: £450.00, 3 days overdue"})
    assert "approve 4" in msg and "reject 4" in msg


# ---------------------------------------------------------------- scenarios (in-memory DB, fake Gemini)
from urllib.parse import unquote  # noqa: E402

import pytest  # noqa: E402

from backend.ai import overdue  # noqa: E402
from backend.db import client as dbclient, schema  # noqa: E402
from bridge.main import parse_reply  # noqa: E402
from tests.conftest import TOKEN, make_invoice  # noqa: E402

web = TestClient(app)


def _flash(r):
    return unquote(r.headers.get("location", "").split("msg=")[-1])


# --- WhatsApp reply parsing: only an unambiguous single command can move money
@pytest.mark.parametrize("text,expected", [
    ("approve 1", ("approve", 1)), ("Approve #2!", ("approve", 2)), ("  yes 3. ", ("approve", 3)), ("OK 4", ("approve", 4)),
    ("approved 5", ("approve", 5)), ("reject 2", ("reject", 2)), ("No 7", ("reject", 7)), ("REJECTED #8", ("reject", 8)),
])
def test_reply_parser_accepts_clear_commands(text, expected):
    assert parse_reply(text) == expected


@pytest.mark.parametrize("text", [
    "don't approve 1", "do not approve 1", "approve 1 and 2", "approve the £450 one", "approve", "1", "",
    "approve -1", "approve 1.5", "yes please approve 3", "approve 1234567", "nope 3",
])
def test_reply_parser_refuses_negated_or_ambiguous_messages(text):
    assert parse_reply(text) is None


# --- /approve (Saint relays the owner's WhatsApp reply)
def test_whatsapp_approve_flow_and_errors(db, gemini_down, sent):
    overdue.run()
    post = lambda body, **kw: web.post("/approve", params={"token": TOKEN}, json=body, **kw)  # noqa: E731
    assert post({"text": "don't approve 1"}).status_code == 400
    assert db[schema.APPROVALS].find_one({"ref": 1})["status"] == "pending"  # the negation moved nothing
    assert post({"text": "approve 1 and 2"}).status_code == 400
    assert post({"ref": "abc", "decision": "approve"}).status_code == 400
    assert post({"ref": True, "decision": "approve"}).status_code == 400
    assert post({"ref": 1, "decision": "edit"}).status_code == 400
    assert post({"ref": 999, "decision": "approve"}).status_code == 404
    r = post({"text": "Approve #1"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["reply"].startswith("✅ #1")
    assert post({"text": "approve 1"}).status_code == 409  # already decided
    r = post({"ref": "2", "decision": "reject"})
    assert r.status_code == 200 and r.json()["reply"].startswith("❌ #2")


def test_token_guard_handles_odd_tokens(db):
    for bad in ["", "wrong", "tést-ünïcode", TOKEN + "x"]:
        assert web.get("/brief", params={"token": bad}).status_code == 403
    assert web.get("/brief", headers={"x-token": TOKEN}).status_code == 200


# --- dashboard
def test_dashboard_renders_with_real_data(db, gemini_down):
    overdue.run()
    r = web.get("/", params={"msg": "<b>hi</b>"})
    assert r.status_code == 200 and "Waiting for your approval" in r.text
    assert "<b>hi</b>" not in r.text and "&lt;b&gt;hi&lt;/b&gt;" in r.text  # flash messages are escaped


def test_dashboard_decision_with_a_bad_id_does_not_crash(db):
    r = web.post("/approvals/not-an-id/decide", data={"decision": "approve"}, follow_redirects=False)
    assert r.status_code == 303 and _flash(r) == "Approval not found."


def test_dashboard_edit_and_approve(db, gemini_down, sent):
    overdue.run()
    a = db[schema.APPROVALS].find_one({"ref": 4})
    r = web.post(f"/approvals/{a['_id']}/decide", data={"decision": "edit", "draft": "Edited text"}, follow_redirects=False)
    assert "#4 edited" in _flash(r) and sent[-1]["draft"] == "Edited text"


def test_upload_route(db, fake_extraction):
    fake_extraction(make_invoice(invoice_number="EDC-5555"))
    r = web.post("/upload", files={"file": ("inv.png", b"img", "image/png")}, follow_redirects=False)
    assert "Added EDC-5555" in _flash(r)
    r = web.post("/upload", files={"file": ("notes.txt", b"hello", "text/plain")}, follow_redirects=False)
    assert "Can't read" in _flash(r)


# --- /brief
def test_brief_escapes_untrusted_names_in_html(db):
    from bson import ObjectId
    evil = db[schema.INVOICES].find_one({"invoice_number": "EDC-7781"})
    evil.update(_id=ObjectId(), invoice_number="X-1", counterparty="<script>alert(1)</script>", counterparty_key="evil",
                needs_review=True, verification_issues=["odd"])
    db[schema.INVOICES].insert_one(evil)  # e.g. a name read from an emailed invoice
    r = web.get("/brief", params={"token": TOKEN, "format": "html"})
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


def test_brief_reply_hint_uses_a_real_ref(db, gemini_down):
    dbclient.next_refs(db, "gaia-coffee", 4)  # refs 1-4 already used earlier today
    overdue.run()
    text = web.get("/brief", params={"token": TOKEN}).text
    assert 'Reply "approve 5"' in text and 'approve 1"' not in text


# --- /updates (Saint's outbox feed)
def test_updates_feed_delivers_each_message_once(db, fake_extraction, sent):
    fake_extraction(make_invoice(issuer_name="GAIA Coffee", recipient_name="Test Cafe", invoice_number="GAIA-4000",
                                 due_date="2026-10-01"))
    web.post("/upload", files={"file": ("inv.png", b"img", "image/png")})
    get = lambda **kw: web.get("/updates", params={"token": TOKEN, **kw}).json()  # noqa: E731
    first = get()
    kinds = [u["kind"] for u in first["updates"]]
    assert "invoice_extracted" in kinds and "approval_needed" in kinds
    assert any("GAIA-4000" in u["message"] for u in first["updates"])
    assert get()["count"] == 0  # delivered once
    assert web.get("/updates", params={"token": TOKEN, "format": "text"}).text == ""  # nothing to send

    ref = next(u["ref"] for u in first["updates"] if u["kind"] == "approval_needed")
    web.post("/approve", params={"token": TOKEN}, json={"text": f"approve {ref}"})  # WhatsApp: owner already got a reply
    assert get()["count"] == 0
    a = db[schema.APPROVALS].find_one({"status": "pending"})
    web.post(f"/approvals/{a['_id']}/decide", data={"decision": "reject"})  # dashboard: tell them on WhatsApp
    msgs = get()["updates"]
    assert len(msgs) == 1 and msgs[0]["message"].startswith("❌")


def test_updates_peek_and_bad_cursor(db, gemini_down):
    overdue.run()
    peek = web.get("/updates", params={"token": TOKEN, "peek": "true"}).json()
    assert peek["count"] == 4 and web.get("/updates", params={"token": TOKEN}).json()["count"] == 4  # peek didn't consume
    assert web.get("/updates", params={"token": TOKEN, "since": "nope"}).status_code == 400
