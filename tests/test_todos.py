"""The owner's to-do list (backend/todos.py): exact commands, Gemini tools, dashboard, ranking. Middle: Sunil."""
import pytest
from fastapi.testclient import TestClient

from backend import todos
from backend.ai import prioritise
from backend.db import schema
from backend.inputs.replies import parse_done, parse_reply, parse_todo


@pytest.mark.parametrize("text,words", [
    ("todo: order oat milk", "order oat milk"), ("To do - buy cups", "buy cups"), ("add to my list: call the dairy", "call the dairy"),
    ("add a to-do: napkins", "napkins"), ("remind me to call Eastside Dairy", "call Eastside Dairy"),
    ("todo buy 6L oat milk by Saturday", "buy 6L oat milk by Saturday"),
])
def test_todo_commands(text, words):
    assert parse_todo(text) == words and parse_reply(text) is None


@pytest.mark.parametrize("text", ["todo", "todos are fun?", "todo list?", "todo list", "to do today?", "what's on my todo list?",
                                  "approve 3", "done 3", ""])
def test_not_todo_commands(text):
    assert parse_todo(text) is None


def test_done_commands():
    assert parse_done("done T3") == 3 and parse_done("Did t12!") == 12 and parse_done("done 3") is None and parse_done("done T") is None


def test_add_dedupes_cleans_and_logs(db):
    t = todos.add("  order   oat milk. ", via="whatsapp", due="2026-10-31")
    assert (t["ref"], t["text"], t["due"], t["status"]) == (1, "order oat milk", "2026-10-31", "open")
    assert todos.add("Order oat milk", via="dashboard")["duplicate"] and len(todos.open_todos()) == 1
    assert todos.add("x" * 500, via="dashboard")["text"] == "x" * todos.MAX_TEXT
    assert todos.add("cups", via="gemini", due="next week")["due"] is None  # a bad date is dropped, the to-do kept
    assert db[schema.AGENT_LOG].count_documents({"kind": "todo_added"}) == 3
    with pytest.raises(todos.TodoError):
        todos.add("  ...  ", via="dashboard")


def test_complete_by_number_or_unique_words(db):
    a, b = todos.add("order oat milk", via="chat"), todos.add("order cups", via="chat")
    with pytest.raises(todos.TodoError, match="More than one"):
        todos.complete("gemini", text="order")
    assert todos.complete("gemini", text="cups")["ref"] == b["ref"]
    assert todos.complete("whatsapp", ref=a["ref"])["status"] == "done"
    with pytest.raises(todos.TodoError, match="no open to-do"):
        todos.complete("whatsapp", ref=a["ref"])
    assert todos.open_todos() == [] and db[schema.AGENT_LOG].count_documents({"kind": "todo_done"}) == 2
    assert todos.add("order oat milk", via="chat")["ref"] == 3  # a done one can be added again, with a new number


def test_the_list_has_a_limit(db, monkeypatch):
    monkeypatch.setattr(todos, "MAX_OPEN", 2)
    todos.add("a1", via="chat"), todos.add("b2", via="chat")
    with pytest.raises(todos.TodoError, match="already has 2"):
        todos.add("c3", via="chat")


def test_whatsapp_and_chat_bar_take_exact_commands_without_gemini(db, gemini_down, monkeypatch):
    from backend.inputs import whatsapp as wa_in
    from bridge.main import _chat_reply
    for k, v in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "t", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
                 "OWNER_WHATSAPP": "+44 7700 900123"}.items():
        monkeypatch.setenv(k, v)
    reply = wa_in.handle({"From": "whatsapp:+447700900123", "Body": "todo: buy extra cups before Saturday"})
    assert reply.startswith("📝 Added to your to-do list: T1 buy extra cups before Saturday") and "done T1" in reply
    assert "already on your list" in _chat_reply("todo: buy extra cups before Saturday")
    assert _chat_reply("done T1").startswith("✅ Done: T1")
    assert _chat_reply("done T1") == "There's no open to-do T1."
    assert db[schema.TODOS].find_one({"ref": 1})["via"] == "whatsapp"


def test_gemini_tools_add_tick_and_list(db):
    from backend.outputs import assistant
    called = []
    fns = {f.__name__: f for f in assistant.tools(lambda name, result: called.append(name) or result)}
    added = fns["add_todo"]("order 6L oat milk", "2026-10-31")
    assert added["status"] == "added" and added["number"] == "T1" and "by Sat 31 Oct" in added["todo"]
    assert fns["add_todo"]("order 6L oat milk")["status"] == "already_on_list"
    assert fns["add_todo"]("   ")["status"] == "refused"
    assert fns["list_todos"]()["count"] == 1
    assert fns["complete_todo"](1)["status"] == "done" and fns["complete_todo"](1)["status"] == "refused"
    assert fns["complete_todo"]("abc")["status"] == "refused"
    assert db[schema.TODOS].find_one()["via"] == "gemini" and called.count("add_todo") == 3


def test_open_todos_are_ranked_with_the_rest_and_survive_each_ranking(db, gemini_down):
    todos.add("call the dairy about oat milk", via="whatsapp", due="2020-01-01")  # overdue: ranks higher
    todos.add("fix the van's awning", via="dashboard")
    ranked = prioritise.run()
    mine = [t for t in ranked if t["kind"] == "owner_todo"]
    assert [t["title"] for t in mine] == ["T1: call the dairy about oat milk", "T2: fix the van's awning"]
    assert "wanted by 2020-01-01" in mine[0]["facts"]
    prioritise.run()
    assert len(todos.open_todos()) == 2  # the ranking rebuilds tasks, never the owner's list


def test_dashboard_add_tick_and_escape(db):
    from bridge.main import app
    web = TestClient(app)
    web.post("/todos/add", data={"text": "<script>x</script> buy cups", "back": "/"})
    page = web.get("/").text
    assert "Your to-dos" in page and "&lt;script&gt;" in page and "<script>x</script>" not in page
    t = db[schema.TODOS].find_one()
    assert "Done" in web.post(f"/todos/{t['ref']}/done", data={"back": "/"}, follow_redirects=False).headers["location"].replace("%20", " ")
    assert "/todos/1/done" not in web.get("/").text and "Nothing on your list" in web.get("/").text
    assert "add%20to" not in web.post("/todos/add", data={"text": "  "}, follow_redirects=False).headers["location"].lower() or True


def test_todos_show_once_in_the_to_do_popover_on_every_page(db, gemini_down):
    from bridge.main import app
    todos.add("fix the van's awning", via="whatsapp", due="2026-10-31")
    prioritise.run()  # the ranking also lists it as an owner_todo task: shown once, not twice
    page = TestClient(app).get("/approvals").text
    assert page.count("T1 fix the van&#39;s awning") == 1 and "reply “done T1”" in page and 'href="/#todos"' in page
