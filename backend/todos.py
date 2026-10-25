"""The owner's own to-do list (MVP3): things they ask CoFoundr to remember, by WhatsApp, the chat bar or Gemini.

Kept apart from `tasks`, which backend.ai.prioritise rebuilds on every run; open to-dos go into that ranking as tasks
of kind "owner_todo", so Gemini can say how they fit the day. Not money: adding or ticking one needs no approval,
but every change is logged in agent_log.

    python -m backend.todos                     # the open list
    python -m backend.todos --add "buy cups"    # add one
"""
import argparse
import re
from datetime import date, datetime, timezone
from typing import List, Optional

from pymongo import ReturnDocument

from backend.db import activity, schema
from backend.db.client import get_db

BUSINESS_ID = "gaia-coffee"
MAX_TEXT = 200
MAX_OPEN = 50  # a list nobody can read on a phone isn't a list


class TodoError(Exception):
    pass


def clean(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" .-–—:")[:MAX_TEXT]


def open_todos(limit: int = MAX_OPEN) -> List[dict]:
    return list(get_db()[schema.TODOS].find({"business_id": BUSINESS_ID, "status": "open"}).sort("ref", 1).limit(limit))


def add(text: str, via: str, due: Optional[str] = None) -> dict:
    """Add one to-do (or return the open one with the same words). `due` = "YYYY-MM-DD" or None."""
    text = clean(text)
    if not re.search(r"\w", text):
        raise TodoError("What should I add? e.g. “todo: order oat milk”.")
    if due is not None and due != "":
        try:
            due = date.fromisoformat(due).isoformat()
        except ValueError:
            due = None
    db = get_db()
    todos = db[schema.TODOS]
    same = todos.find_one({"business_id": BUSINESS_ID, "status": "open", "key": _key(text)})
    if same:
        return {**same, "duplicate": True}
    if todos.count_documents({"business_id": BUSINESS_ID, "status": "open"}) >= MAX_OPEN:
        raise TodoError(f"Your list already has {MAX_OPEN} open to-dos. Tick some off first.")
    ref = db[schema.COUNTERS].find_one_and_update({"_id": f"{BUSINESS_ID}:todo_ref"}, {"$inc": {"seq": 1}}, upsert=True,
                                                  return_document=ReturnDocument.AFTER)["seq"]
    doc = {"business_id": BUSINESS_ID, "ref": ref, "text": text, "key": _key(text), "due": due or None, "status": "open",
           "via": via, "created_at": datetime.now(timezone.utc), "done_at": None, "synthetic": True}
    todos.insert_one(doc)
    activity.log("todo_added", f"To-do T{ref} added via {via}: {text}", actor="owner", todo_ref=ref, via=via)
    return doc


def complete(via: str, ref: Optional[int] = None, text: Optional[str] = None) -> dict:
    """Tick off one open to-do by its number, or by words that match exactly one open to-do."""
    db = get_db()
    query = {"business_id": BUSINESS_ID, "status": "open"}
    if ref is not None:
        found = db[schema.TODOS].find_one({**query, "ref": int(ref)})
        if not found:
            raise TodoError(f"There's no open to-do T{ref}.")
    else:
        words = set(_key(text or "").split())
        if not words:
            raise TodoError("Which to-do? Give its number, e.g. T3.")
        hits = [t for t in db[schema.TODOS].find(query) if words <= set(t["key"].split())]
        if len(hits) != 1:
            raise TodoError("No open to-do matches that." if not hits else
                            "More than one to-do matches: " + ", ".join(f"T{t['ref']} {t['text']}" for t in hits[:5]))
        found = hits[0]
    now = datetime.now(timezone.utc)
    if not db[schema.TODOS].update_one({"_id": found["_id"], "status": "open"}, {"$set": {"status": "done", "done_at": now}}).modified_count:
        raise TodoError(f"T{found['ref']} was already done.")
    activity.log("todo_done", f"To-do T{found['ref']} done via {via}: {found['text']}", actor="owner", todo_ref=found["ref"], via=via)
    return {**found, "status": "done", "done_at": now}


def line(t: dict) -> str:
    return f"T{t['ref']} {t['text']}" + (f" (by {date.fromisoformat(t['due']):%a %d %b})" if t.get("due") else "")


def _key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9£.]+", (text or "").lower()))


def main() -> None:
    parser = argparse.ArgumentParser(description="The owner's to-do list.")
    parser.add_argument("--add", metavar="TEXT")
    args = parser.parse_args()
    if args.add:
        print("added", line(add(args.add, via="cli")))
    for t in open_todos():
        print(" ", line(t))


if __name__ == "__main__":
    main()
