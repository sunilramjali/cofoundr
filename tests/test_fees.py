"""Card fee audit (owner: Sunil, MVP2): Gemini reads the fee summary, its own worked examples check the reading,
code audits every payment. Fake Gemini, in-memory MongoDB. Own file: no conflicts with test_edges/test_middle."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import payments
from backend.ai import fees, prioritise
from backend.ai.fees import Example, FeeSchedule, Rate
from backend.db import schema
from backend.db.client import RealDataRefused
from backend.inputs import evo
from data import make_evo_sample, make_fee_sample

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def _schedule(debit=0.30, example_total=0.09, synthetic=True, **over):
    rates = [Rate(card=c, channel="in_person", pct=p, fixed_gbp=0.03) for c, p in (("debit", debit), ("credit", 0.6), ("business", 1.2))]
    return FeeSchedule(**{"provider": "EXAMPLE PAYMENTS (SYNTHETIC)", "contract_end": "2027-06-30", "rates": rates, "auth_fee_gbp": 0.03,
                          "min_monthly_gbp": 10.0, "marked_synthetic": synthetic,
                          "examples": [Example(description="£10 debit in person", amount_gbp=10, card="debit", channel="in_person",
                                               total_fee_gbp=example_total)], **over})


@pytest.fixture
def sample_payments(db):
    payments.record_payments(evo.parse(make_evo_sample.OUT.read_bytes()).payments, origin="test", synthetic=True, now=NOW)
    return db


@pytest.fixture
def reads(monkeypatch):
    """Gemini 'reads' the given schedules in turn; records each prompt."""
    prompts = []

    def use(*schedules):
        queue = list(schedules)
        monkeypatch.setattr(fees, "ask_gemini", lambda data, mime, prompt: prompts.append(prompt) or queue.pop(0))
        return prompts
    return use


# --- the page's own worked example checks the reading

def test_a_correct_reading_reproduces_the_worked_example():
    assert fees.verify(_schedule()) == []


@pytest.mark.parametrize("schedule, problem", [
    (_schedule(debit=0.03), "the rates read give £0.06, the page says £0.09"),  # misread 0.30% as 0.03%
    (_schedule(examples=[]), "no worked example"),
    (_schedule(rates=[]), "no in-person debit rate"),
    (_schedule(debit=30), "isn't a plausible card fee"),
    (_schedule(contract_end="30 June 2027"), "isn't a YYYY-MM-DD date"),
])
def test_a_misreading_is_caught(schedule, problem):
    assert any(problem in p for p in fees.verify(schedule))


def test_a_misreading_is_re_read_once_with_the_problems(reads):
    prompts = reads(_schedule(debit=0.03), _schedule())
    schedule, problems, attempts = fees.read_schedule(b"img", "image/png")
    assert problems == [] and attempts == 2 and "the page says £0.09" in prompts[1]


# --- audit (code)

def test_every_planted_overcharge_is_found_and_nothing_else(sample_payments):
    a = fees.audit(list(sample_payments[schema.PAYMENTS].find()), _schedule())
    rows = evo.parse(make_evo_sample.OUT.read_bytes()).payments  # same order as the generator wrote them
    planted = {(p["paid_at"], p["amount_gbp"]) for n, p in enumerate(rows, 1)
               if n % make_evo_sample.OVERCHARGE_EVERY == 0 and p["region"] == "domestic"}  # overseas cards aren't covered
    assert planted and {(o["date"], o["amount"]) for o in a["overcharged"]} == planted
    assert all(o["extra"] == make_evo_sample.OVERCHARGE for o in a["overcharged"])
    assert a["matched"] + len(a["overcharged"]) == a["checked"] and a["not_covered"] > 0


def test_months_under_the_minimum_charge_are_flagged():
    few = [{"date": "2026-10-03", "amount": 4.0, "fee": 0.04, "kind": "sale", "region": "domestic", "card_product": "debit"}]
    a = fees.audit(few, _schedule())
    assert a["below_minimum"] == [{"month": "2026-10", "fees": 0.04, "minimum": 10.0, "topped_up_by": 9.96}]


def test_refunds_payments_without_a_fee_and_unknown_cards_are_not_judged():
    odd = [{"date": "2026-10-03", "amount": 4.0, "fee": 0.5, "kind": "refund", "region": "domestic", "card_product": "debit"},
           {"date": "2026-10-03", "amount": 4.0, "fee": None, "kind": "sale", "region": "domestic", "card_product": "debit"},
           {"date": "2026-10-03", "amount": 4.0, "fee": 0.5, "kind": "sale", "region": "domestic", "card_product": None}]
    a = fees.audit(odd, _schedule())
    assert a["checked"] == 0 and a["overcharged"] == [] and a["not_covered"] == 1


# --- run, task, refusal, page

def test_run_stores_the_audit_and_the_overcharge_becomes_a_ranked_task(sample_payments, reads):
    reads(_schedule())
    doc = fees.run(b"img", "image/png", now=NOW)
    assert doc["audit"]["overcharged"] and sample_payments[schema.FEE_AUDITS].count_documents({}) == 1
    tasks = prioritise.fee_tasks(sample_payments)
    assert len(tasks) == 1 and "EXAMPLE PAYMENTS (SYNTHETIC)" in tasks[0]["title"] and tasks[0]["kind"] == "fee_overcharge"
    assert "overcharged" in sample_payments[schema.AGENT_LOG].find_one({"kind": "fee_audit"})["summary"]


def test_an_untrusted_reading_is_stored_for_the_owner_but_never_audited(sample_payments, reads):
    reads(_schedule(debit=0.03), _schedule(debit=0.03))
    doc = fees.run(b"img", "image/png", now=NOW)
    assert doc["audit"] is None and doc["attempts"] == 2 and prioritise.fee_tasks(sample_payments) == []
    assert "not trusted" in sample_payments[schema.AGENT_LOG].find_one({"kind": "fee_audit"})["summary"]


def test_a_real_fee_summary_is_refused_on_the_demo_database(db, reads, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", schema.DB_NAME)
    reads(_schedule(synthetic=False))
    with pytest.raises(RealDataRefused):
        fees.run(b"img", "image/png", now=NOW)
    assert db.client[schema.DB_NAME][schema.FEE_AUDITS].count_documents({}) == 0


def test_the_sales_page_upload_reports_the_audit(sample_payments, reads):
    from bridge.main import app
    reads(_schedule())
    web = TestClient(app)
    r = web.post("/import/fees", files={"file": ("fees.png", make_fee_sample.OUT.read_bytes(), "image/png")}, follow_redirects=False)
    assert r.status_code == 303 and "overcharged" in r.headers["location"]
    page = web.get("/sales").text
    assert "overcharged" in page and "worked example" in page
    bad = web.post("/import/fees", files={"file": ("fees.txt", b"hello", "text/plain")}, follow_redirects=False)
    assert "photo or PDF" in bad.headers["location"].replace("%20", " ")


def test_gemini_down_on_upload_gives_a_plain_message(db, gemini_down):
    from bridge.main import app
    r = TestClient(app).post("/import/fees", files={"file": ("f.png", b"img", "image/png")}, follow_redirects=False)
    assert "couldn" in r.headers["location"] and db[schema.FEE_AUDITS].count_documents({}) == 0
