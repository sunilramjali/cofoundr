"""Dashboard proof (Sunil, in Jade's bridge/ area): Home's "While you were away" + "Do first", the board's
"the database check caught" chips, and Atlas hybrid search on Finances. In-memory DB, no Gemini."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.db import schema
from bridge import view
from bridge.main import app

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
MEMO = {"_id": "gaia-coffee:2026-10-25", "business_id": "gaia-coffee", "date": "2026-10-25", "headline": "Cash is stuck",
        "attempts": 2, "model": "gemini-4-argon", "generated_at": NOW, "synthetic": True,
        "read": {"tokens": 169208, "sales_lines": 4353, "invoices": 16},
        "insights": [{"title": "Pastries", "finding": "We sold 8 Fresh Pastries (£25.60).", "action": "Bake less.",
                      "impact_gbp_per_month": 20, "status": "corrected", "claims": [], "problems": [],
                      "first_problems": ["'10 Fresh Pastries': you said 10, the database says 8",
                                         "'£32.00': you said 32, the database says 25.6",
                                         "'a trend': can't be checked (no such kind)"]}]}


def test_board_fixes_reads_only_number_mismatches_and_formats_money():
    fixes = view.board_fixes(MEMO["insights"][0]["first_problems"])
    assert [(f["says"], f["shown"]) for f in fixes] == [("10 Fresh Pastries", "8"), ("£32.00", "£25.60")]
    assert view.board_fixes(None) == [] and view.board_fixes(["garbage", ""]) == []
    assert view.board_fixes(["'£1,105.00 in sales': you said 1105, the database says 3048.3"])[0]["shown"] == "£3,048.30"


def test_while_away_says_only_what_was_stored():
    assert view.while_away([], [], None, None, None) == []
    worse = {"overall": {"days": 11, "baseline_error": 5.4, "adjusted_error": 7.9, "helped": 4}, "cap": 20.0}
    fee = {"audit": {"checked": 888, "matched": 884, "extra_total": 0.6, "overcharged": [{}] * 4}}
    lines = [w["text"] for w in view.while_away([{}, {}], [{"_id": 1}], fee, MEMO, worse)]
    assert lines[0] == "Read 2 invoices this week and checked every total, and flagged 1 invoice that doesn't add up."
    assert "4 overcharged card payments (£0.60)" in lines[1] and "888" in lines[1]
    assert "169,208 tokens" in lines[2] and "caught 2 wrong numbers" in lines[2]
    assert "made them worse (5.4% → 7.9% off)" in lines[3] and "±20%" in lines[3]
    better = {"overall": {"days": 10, "baseline_error": 8.0, "adjusted_error": 6.1, "helped": 7}, "cap": 35.0}
    only = view.while_away([], [{"_id": 1}, {"_id": 2}], {"audit": {"checked": 5, "overcharged": []}}, None, better)
    assert only[0]["text"] == "Flagged 2 invoices that don't add up." and "every fee matched" in only[1]["text"]
    assert "helped (8.0% → 6.1% off) on 7 of 10 days" in only[2]["text"]
    no_tokens = {**MEMO, "read": {}, "insights": [{**MEMO["insights"][0], "first_problems": []}]}
    assert view.while_away([], [], None, no_tokens, None)[0]["text"].endswith("every number in the memo checked against your records.")


def test_home_shows_what_needs_a_yes_what_i_did_and_what_to_do_first(db):
    web = TestClient(app)
    db[schema.BOARD_MEMOS].insert_one(MEMO)
    db[schema.TASKS].insert_one({"_id": "gaia-coffee:prep_day:2026-10-26", "business_id": "gaia-coffee", "kind": "prep_day",
                                 "title": "Prep for Monday (Mon 26 Oct): ~£599 expected", "reason": "Busy start to the week.",
                                 "rank": 1, "impact": "high", "ranked_by": "gemini", "ranked_at": NOW, "synthetic": True})
    page = web.get("/").text
    assert "While you were away, I…" in page and "169,208 tokens" in page and "caught 2 wrong numbers" in page
    assert "Do first" in page and "Prep for Monday (Mon 26 Oct)" in page and "Busy start to the week." in page
    assert page.index("While you were away") < page.index("Do first")


def test_home_with_nothing_stored_still_renders(db):
    for name in (schema.INVOICES, schema.APPROVALS):
        db[name].delete_many({})
    page = TestClient(app).get("/").text
    assert "All clear." in page and "While you were away" not in page and "Do first" not in page


def test_board_page_shows_what_the_database_check_caught(db):
    db[schema.BOARD_MEMOS].insert_one(MEMO)
    page = TestClient(app).get("/board").text
    assert "The database check caught:" in page and "<s>10 Fresh Pastries</s> → <b>8</b>" in page
    assert "<s>£32.00</s> → <b>£25.60</b>" in page and "wrong numbers caught before you saw them" in page
    assert "tokens read in one Gemini call" in page


def test_invoice_search_on_finances(db):
    web = TestClient(app)
    plain = web.get("/invoices").text
    assert 'name="q"' in plain and "match" not in plain.split("Search invoices")[1].split("</form>")[1][:200]
    found = web.get("/invoices", params={"q": "Eastside"}).text
    assert "for “Eastside”" in found and "Eastside Dairy" in found and "plain word match" in found  # mongomock: no Atlas
    assert "Nothing matched." in web.get("/invoices", params={"q": "zzzz-no-such"}).text
    assert web.get("/invoices", params={"q": "<script>x</script>"}).text.count("<script>x</script>") == 0  # escaped
    assert web.get("/invoices", params={"q": "x" * 5000}).status_code == 200
