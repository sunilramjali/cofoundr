"""Owner-decision memory (backend/memory.py) + the "Under the hood" page (backend/db/showcase.py). Middle: Sunil."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure

from backend import approvals, memory
from backend.ai import overdue
from backend.db import schema, search, showcase


def _approval_for(db, number):
    return db[schema.APPROVALS].find_one({"invoice_number": number, "status": "pending"})


def _memories(db):
    return list(db[schema.MEMORIES].find().sort("at", 1))


class Fake:
    """Answers each aggregate with the next canned result (or raises it); find() returns `docs`."""
    def __init__(self, *answers, docs=()):
        self.answers, self.pipelines, self.docs, self.logged = list(answers), [], list(docs), []

    def __getitem__(self, _name):
        return self

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        answer = self.answers.pop(0) if self.answers else []
        if isinstance(answer, Exception):
            raise answer
        return iter(answer)

    def find(self, *_a, **_k):
        return Rows(self.docs)

    def find_one(self, *_a, **_k):
        return None

    def insert_one(self, doc, *_a, **_k):
        self.logged.append(doc)

    def count_documents(self, *_a, **_k):
        return len(self.docs)


class Rows(list):
    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        return Rows(self[:n])


# ---------------------------------------------------------------- remember

def test_a_rejection_with_a_reason_is_remembered_and_kept_on_the_approval(db, gemini_down, sent):
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    approvals.decide(a, "reject", via="dashboard", note="  They're friends:\n  they always pay, just late. ")
    assert db[schema.APPROVALS].find_one({"_id": a["_id"]})["owner_note"] == "They're friends: they always pay, just late."
    [m] = _memories(db)
    assert (m["decision"], m["via"], m["counterparty_key"], m["approval_id"]) == ("rejected", "dashboard", "hackneyhubcoworking", str(a["_id"]))
    assert m["outcome"] is None and m["synthetic"] is True and sent == []
    assert "owner: They're friends" in m["search_text"] and "Hackney Hub Coworking" in m["search_text"]
    assert "you said no to a friendly nudge to Hackney Hub Coworking" in memory.line(m) and "“They're friends" in memory.line(m)
    logged = db[schema.AGENT_LOG].find_one({"kind": "approval_decided"})
    assert logged["detail"]["note"] == "They're friends: they always pay, just late."


def test_no_reason_is_fine_and_a_long_one_is_cut(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "approve", via="whatsapp", note="   ")
    a = _approval_for(db, "GAIA-1038")
    approvals.decide(a, "reject", via="dashboard", note="x" * 900)
    first, second = _memories(db)
    assert first["note"] is None and "owner_note" not in db[schema.APPROVALS].find_one({"invoice_number": "GAIA-1041"})
    assert len(second["note"]) == memory.MAX_NOTE


def test_an_edit_remembers_how_the_draft_changed(db, gemini_down, sent):
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    approvals.decide(a, "edit", via="dashboard", edited_draft="Hi! Any news on GAIA-1041? Thanks, GAIA Coffee")
    [m] = _memories(db)
    assert m["decision"] == "edited" and m["edit"].startswith("shortened the draft from ")
    assert "you rewrote" in memory.line(m) and "you shortened the draft" in memory.line(m)


def test_one_memory_per_approval_and_backfill_is_idempotent(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "approve", via="dashboard")
    assert memory.backfill(db) == 0  # already remembered
    db[schema.MEMORIES].delete_many({})
    assert memory.backfill(db) == 1 and memory.backfill(db) == 0 and len(_memories(db)) == 1


def test_a_failed_send_is_not_remembered_as_a_decision(db, gemini_down, sent):
    overdue.run()
    sent.fail = True
    a = _approval_for(db, "GAIA-1041")
    with pytest.raises(approvals.DecisionError):
        approvals.decide(a, "approve", via="dashboard", note="go ahead")
    back = db[schema.APPROVALS].find_one({"_id": a["_id"]})
    assert back["status"] == "pending" and "owner_note" not in back and _memories(db) == []


def test_a_broken_memory_never_undoes_the_decision(db, gemini_down, sent, monkeypatch):
    overdue.run()
    monkeypatch.setattr(memory, "remember", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    a = _approval_for(db, "GAIA-1041")
    assert approvals.decide(a, "approve", via="dashboard")["status"] == "approved"
    assert len(sent) == 1 and db[schema.AGENT_LOG].find_one({"kind": "memory_failed"})


def test_cancelled_actions_are_not_remembered(db):
    assert memory.remember(db, {"_id": "x", "target_id": "bad"}, {"status": "cancelled"}) is None


# ---------------------------------------------------------------- outcomes

def test_outcomes_are_written_back_chased_again_then_paid(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "approve", via="dashboard")
    inv = db[schema.INVOICES].find_one({"invoice_number": "GAIA-1041"})
    later = datetime.now(timezone.utc) + timedelta(days=4)
    # four days on, still unpaid: the owner approves a second reminder for the same invoice
    second = {"_id": "second", "ref": 99, "target_id": str(inv["_id"]),
              "action_type": "invoice_reminder", "invoice_number": "GAIA-1041", "counterparty": inv["counterparty"],
              "amount": inv["amount"], "escalation_level": 1, "days_overdue": 7, "business_id": "gaia-coffee"}
    memory.remember(db, second, {"status": "approved", "decided_via": "whatsapp"}, now=later)
    first, again = _memories(db)
    assert first["outcome"]["result"] == "chased_again" and first["outcome"]["days"] == 4 and again["outcome"] is None
    assert "Still unpaid 4 days later, so it was chased again." in memory.line(first)
    db[schema.INVOICES].update_one({"_id": inv["_id"]}, {"$set": {"status": "paid", "paid_at": later + timedelta(days=2)}})
    assert memory.settle(db) == 1 and memory.settle(db) == 0
    again = db[schema.MEMORIES].find_one({"approval_id": "second"})
    assert again["outcome"]["result"] == "paid" and again["outcome"]["days"] == 2 and memory.line(again).endswith("Paid 2 days later.")


def test_rejections_get_no_outcome(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "reject", via="dashboard")
    db[schema.INVOICES].update_one({"invoice_number": "GAIA-1041"}, {"$set": {"status": "paid"}})
    assert memory.settle(db) == 0


# ---------------------------------------------------------------- recall (before drafting)

def test_next_draft_recalls_the_owners_reason_for_that_customer(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1041"), "reject", via="whatsapp", note="give them two weeks, they're regulars")
    [again] = [a for a in overdue.run() if a["invoice_number"] == "GAIA-1041"]
    [m] = again["memories"]
    assert m["same_counterparty"] and m["note"] == "give them two weeks, they're regulars" and m["score"] is None
    assert again["reason"].endswith("Last time you said: “give them two weeks, they're regulars”.")
    assert again["drafted_by"] == "template"  # Gemini down: memory still shown, nothing breaks
    stored = _approval_for(db, "GAIA-1041")
    assert stored["memories"][0]["line"] == m["line"]


def test_the_owners_past_decisions_go_into_geminis_prompt_not_the_email(db, monkeypatch):
    prompts = []

    class Client:
        class models:
            @staticmethod
            def generate_content(model, contents, config):
                prompts.append(contents)
                raise RuntimeError("stop here")
    monkeypatch.setattr(overdue, "get_client", lambda: Client)
    case = db[schema.INVOICES].find_one({"invoice_number": "GAIA-1041"})
    recalled = {str(case["_id"]): [{"line": "24 Oct: you said no: “keep it gentle”.", "same_counterparty": True, "note": "keep it gentle"}]}
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    drafts = overdue.draft_reminders([case], today, recalled)
    assert drafts["GAIA-1041"][1] == "template"
    assert "owner's past decision (same customer: 24 Oct: you said no: “keep it gentle”.)" in prompts[0]
    assert "never mention them" in prompts[0]


def test_similar_cases_come_from_vector_search_and_only_when_they_taught_something():
    hits = [
        {"counterparty": "Hackney Hub Coworking", "counterparty_key": "hackneyhubcoworking", "decision": "rejected", "score": 0.95,
         "action_type": "invoice_reminder", "amount": 1.0, "at": datetime(2026, 10, 20, tzinfo=timezone.utc)},  # own: excluded
        {"counterparty": "Dalston Desk Co", "counterparty_key": "dalstondeskco", "decision": "rejected", "note": "friends",
         "score": 0.82, "action_type": "invoice_reminder", "amount": 80.0, "at": datetime(2026, 10, 21, tzinfo=timezone.utc)},
        {"counterparty": "Plain Approve Ltd", "counterparty_key": "plainapproveltd", "decision": "approved", "score": 0.81,
         "action_type": "invoice_reminder", "amount": 5.0, "at": datetime(2026, 10, 22, tzinfo=timezone.utc)},  # taught nothing
        {"counterparty": "Far Away", "counterparty_key": "faraway", "decision": "rejected", "note": "x", "score": 0.5,
         "action_type": "invoice_reminder", "amount": 5.0, "at": datetime(2026, 10, 22, tzinfo=timezone.utc)},  # too far
    ]
    atlas = Fake(hits)
    how = {}
    out = memory.recall(atlas, {"counterparty": "Hackney Hub Coworking", "counterparty_key": "hackneyhubcoworking",
                                "action_type": "invoice_reminder", "about": "Coffee catering", "amount": 450.0}, details=how)
    assert len(out) == 1 and "you said no to chasing Dalston Desk Co (£80.00): “friends”." in out[0]["line"]
    assert out[0]["score"] == 0.82 and not out[0]["same_counterparty"] and how["mode"] == "hybrid"
    stage = atlas.pipelines[0][0]["$vectorSearch"]
    assert stage["index"] == search.MEMORIES_SEMANTIC and stage["path"] == "search_text"
    assert stage["filter"] == {"business_id": "gaia-coffee", "action_type": "invoice_reminder"}
    assert "Coffee catering" in stage["query"]["text"]


def test_recall_falls_back_to_shared_words_without_vector_search(db, gemini_down, sent):
    overdue.run()
    approvals.decide(_approval_for(db, "GAIA-1038"), "reject", via="dashboard", note="weddings pay after the season")
    case = memory.case({"counterparty": "Victoria Park Events", "line_items": []}, "invoice_reminder", 9, 1)
    how = {}
    out = memory.recall(db, case, details=how)
    assert how["mode"] == "basic" and out and "weddings pay after the season" in out[0]["line"]
    assert db[schema.AGENT_LOG].find_one({"kind": "search_fallback"})  # never silent


def test_a_silent_vector_index_falls_back_too():
    docs = [{"counterparty": "Dalston Desk Co", "counterparty_key": "dalstondeskco", "decision": "rejected", "note": "friends",
             "search_text": "Dalston Desk Co | chasing a customer | rejected | owner: friends", "action_type": "invoice_reminder",
             "amount": 1.0, "at": datetime(2026, 10, 21, tzinfo=timezone.utc)}]
    r = search.similar_memories(Fake([], docs=docs), "Dalston Desk | chasing a customer", "invoice_reminder")
    assert r.mode == "basic" and r.hits[0]["counterparty"] == "Dalston Desk Co"


def test_recall_never_raises(db):
    class Broken:
        def __getitem__(self, _n):
            raise OperationFailure("cluster on fire")
    assert memory.recall(Broken(), {"counterparty_key": "x", "action_type": "invoice_reminder"}) == []
    assert db[schema.AGENT_LOG].find_one({"kind": "memory_failed"})


# ---------------------------------------------------------------- dashboard

def test_dashboard_reject_with_a_reason_then_the_next_card_shows_it(db, gemini_down, sent):
    from bridge.main import app
    web = TestClient(app)
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    page = web.get("/approvals").text
    assert 'name="note"' in page and "I'll remember it next time" in page
    web.post(f"/approvals/{a['_id']}/decide", data={"decision": "reject", "note": "<b>friends</b>, be gentle", "back": "/approvals"})
    assert db[schema.MEMORIES].find_one()["note"] == "<b>friends</b>, be gentle"
    overdue.run()
    page = web.get("/approvals").text
    assert "What I remember" in page and "&lt;b&gt;friends&lt;/b&gt;, be gentle" in page and "<b>friends</b>" not in page


# ---------------------------------------------------------------- under the hood

def test_recorder_keeps_every_pipeline_it_sends():
    atlas = Fake([{"_id": 1}])
    rec = showcase._Recorder(atlas)
    assert list(rec["invoices"].aggregate([{"$search": {"index": "invoices_text"}}])) == [{"_id": 1}]
    assert rec.calls == [("invoices", [{"$search": {"index": "invoices_text"}}])]
    assert showcase._pipeline_text("invoices", [{"$limit": 1}]).startswith("db.invoices.aggregate([")


def test_a_broken_section_never_hides_the_others(db):
    s = showcase._section(db, "X", "t", "w", lambda _db: (_ for _ in ()).throw(KeyError("gone")))
    assert s["mode"] == "unavailable" and "KeyError" in s["error"] and s["rows"] == []


def test_under_the_hood_page_runs_every_section_and_says_when_it_fell_back(db, monkeypatch):
    from bridge.main import app
    monkeypatch.setattr(showcase, "_cache", {"at": 0.0, "sections": None})
    page = TestClient(app).get("/mongodb")
    assert page.status_code == 200
    for feature, *_ in showcase.SECTIONS:
        assert feature.split()[0] in page.text
    assert "The query it sent" in page.text and "$rankFusion" in page.text and "memories_semantic" in page.text
    assert "Fell back to a plain match" in page.text  # mongomock has no Atlas Search: said, not hidden
    assert "Under the hood" in TestClient(app).get("/").text  # in the sidebar


def test_showcase_caches_for_a_minute(db, monkeypatch):
    monkeypatch.setattr(showcase, "_cache", {"at": 0.0, "sections": None})
    first = showcase.run(db)
    assert showcase.run(db) is first and showcase.run(db, fresh=True) is not first


# ---------------------------------------------------------------- post-mortem edge cases (Sun 25 Oct)

def test_remembering_again_never_rewrites_its_own_outcome(db, gemini_down, sent):
    """Bug found in review: a second remember() for the same approval marked its own memory "chased again"."""
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    approvals.decide(a, "approve", via="dashboard")
    decided = db[schema.APPROVALS].find_one({"_id": a["_id"]})
    assert memory.remember(db, decided, decided) is None and memory.backfill(db) == 0
    assert _memories(db)[0]["outcome"] is None


def test_another_customers_name_never_reaches_this_customers_prompt():
    other = {"line": "21 Oct: you said no to chasing Dalston Desk Co: “Dalston Desk Co are friends”.",
             "anonymous_line": None, "same_counterparty": False}
    m = {"counterparty": "Dalston Desk Co", "action_type": "invoice_reminder", "decision": "rejected", "amount": 80.0,
         "note": "dalston desk co are friends", "at": datetime(2026, 10, 21, tzinfo=timezone.utc), "score": 0.8}
    recalled = memory._recalled(m, same=False)
    [text] = memory.prompt_lines([recalled])
    assert "Dalston" not in text and "another customer" in text and "“them are friends”" in text
    assert "Dalston Desk Co" in recalled["line"]  # the owner still sees who it was
    assert memory.prompt_lines([other]) == ["a similar case: another customer's case"]  # older approvals: no name either


def test_same_customer_lines_keep_the_name_in_the_prompt(db):
    m = {"counterparty": "Hackney Hub Coworking", "action_type": "invoice_reminder", "decision": "rejected", "amount": 1.0,
         "at": datetime(2026, 10, 21, tzinfo=timezone.utc)}
    assert "Hackney Hub Coworking" in memory.prompt_lines([memory._recalled(m, same=True)])[0]


def test_dry_run_writes_no_memory_outcomes(db, gemini_down, sent, monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "settle", lambda *_a, **_k: calls.append(1))
    overdue.run(dry_run=True)
    assert calls == [] and db[schema.APPROVALS].count_documents({}) == 0 and _memories(db) == []


def test_naive_dates_from_the_driver_still_format():
    m = {"counterparty": "X", "action_type": "supplier_payment", "decision": "approved", "amount": 2.5,
         "at": datetime(2026, 10, 21, 9), "outcome": None}
    assert memory.line(m) == "21 Oct: you approved paying X (£2.50)."


# ---------------------------------------------------------------- limits fixed (Sun 25 Oct): reasons on WhatsApp, no leaks

from backend.inputs.replies import parse_rejection, parse_reply  # noqa: E402


@pytest.mark.parametrize("text,expected", [
    ("reject 2: they're regulars", (2, "they're regulars")), ("No 2 because they always pay", (2, "they always pay")),
    ("REJECT #3 - friends", (3, "friends")), ("reject 4, busy week for them", (4, "busy week for them")),
    ("reject 2: they pay late but they pay", (2, "they pay late but they pay")),
])
def test_a_no_can_carry_a_reason(text, expected):
    assert parse_rejection(text) == expected and parse_reply(text) is None


@pytest.mark.parametrize("text", [
    "reject 2", "approve 2: go", "yes 2 because why not", "reject 2 and approve 3", "no 2, approve it instead",
    "reject 2 - yes they owe but wait", "reject 2:   ", "don't reject 2: hmm", "reject two: x",
])
def test_anything_unclear_is_not_a_reasoned_no(text):
    assert parse_rejection(text) is None


def test_reject_with_a_reason_on_whatsapp_is_remembered(db, gemini_down, sent, monkeypatch):
    from backend.inputs import whatsapp as wa_in
    for k, v in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "t", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
                 "OWNER_WHATSAPP": "+44 7700 900123"}.items():
        monkeypatch.setenv(k, v)
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    reply = wa_in.handle({"From": "whatsapp:+447700900123", "Body": f"reject {a['ref']}: they always pay, just late"})
    assert reply.startswith("❌") and "I'll remember why" in reply
    [m] = _memories(db)
    assert (m["via"], m["note"], m["decision"]) == ("whatsapp", "they always pay, just late", "rejected")
    plain = wa_in.handle({"From": "whatsapp:+447700900123", "Body": f"reject {_approval_for(db, 'GAIA-1038')['ref']}"})
    assert "remember" not in plain and _memories(db)[1]["note"] is None


def test_reject_with_a_reason_in_the_chat_bar(db, gemini_down, sent):
    from bridge.main import _chat_reply
    overdue.run()
    a = _approval_for(db, "GAIA-1041")
    assert _chat_reply(f"no {a['ref']} because give them two weeks").endswith("I'll remember why.")
    assert db[schema.APPROVALS].find_one({"_id": a["_id"]})["owner_note"] == "give them two weeks"


def test_a_nickname_is_scrubbed_from_another_customers_note_too():
    m = {"counterparty": "Dalston Desk Co", "action_type": "invoice_reminder", "decision": "rejected", "amount": 1.0,
         "note": "the Dalston lot are friends", "at": datetime(2026, 10, 21, tzinfo=timezone.utc), "score": 0.8}
    [text] = memory.prompt_lines([memory._recalled(m, same=False)])
    assert "dalston" not in text.lower() and "the them lot are friends" in text


def test_mentions_other_only_counts_distinctive_words():
    others = ["Victoria Park Wedding Co", "Broadway Market Traders Assoc."]
    assert memory.mentions_other("Thanks for the wedding coffee bar", "Hackney Hub Coworking", others) == "Victoria Park Wedding Co"
    assert memory.mentions_other("Hi Hackney Hub, GAIA Coffee Co here", "Hackney Hub Coworking", others) is None
    assert memory.mentions_other("Your market stall coffee", "Hackney Hub Coworking Market stall coffee", others) is None  # own items


def _gemini_drafts(monkeypatch, drafts):
    from types import SimpleNamespace
    payload = overdue.Drafts(drafts=[overdue.Draft(**d) for d in drafts]).model_dump_json()
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_k: SimpleNamespace(text=payload)))
    monkeypatch.setattr(overdue, "get_client", lambda: client)


def test_a_gemini_email_that_names_another_customer_is_replaced_by_the_template(db, monkeypatch):
    _gemini_drafts(monkeypatch, [
        {"invoice_number": "GAIA-1041", "subject": "Invoice GAIA-1041", "body": "Hi Hackney Hub, like Victoria Park Wedding Co we..."},
        {"invoice_number": "GAIA-1038", "subject": "Invoice GAIA-1038", "body": "Hi Victoria Park Wedding Co, a gentle nudge."}])
    batch = list(db[schema.INVOICES].find({"invoice_number": {"$in": ["GAIA-1041", "GAIA-1038"]}}))
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = overdue.draft_reminders(batch, today)
    assert out["GAIA-1041"][1] == "template" and "Victoria" not in out["GAIA-1041"][0].body
    assert out["GAIA-1038"][1] == "gemini"  # its own name is fine
    assert db[schema.AGENT_LOG].find_one({"kind": "draft_check_failed", "detail.invoice_number": "GAIA-1041"})


def test_a_gemini_email_that_names_a_recalled_customer_is_replaced(db, monkeypatch):
    _gemini_drafts(monkeypatch, [{"invoice_number": "GAIA-1041", "subject": "x", "body": "Hi! Dalston paid late too."}])
    inv = db[schema.INVOICES].find_one({"invoice_number": "GAIA-1041"})
    recalled = {str(inv["_id"]): [{"line": "…", "anonymous_line": "…", "counterparty": "Dalston Desk Co", "same_counterparty": False}]}
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    assert overdue.draft_reminders([inv], today, recalled)["GAIA-1041"][1] == "template"
