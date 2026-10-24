"""Local sandbox: run the dashboard on an in-memory copy of the demo data, so clicking around locally
(approve, pay, upload, chat bar) never changes the shared Atlas database the live demo uses.

    COFOUNDR_SANDBOX=1 uvicorn bridge.main:app --reload --port 8000

On start it reads every collection from Atlas once (read-only) into mongomock and points the app at that copy.
The real inbox check, real email sending and WhatsApp sending are switched off; Gemini still works (uploads are read for real).
Changes vanish when the server restarts.
"""
import mongomock

from backend.db import client
from backend.inputs import gmail
from backend.outputs import email, whatsapp

_mock = None


def install() -> None:
    global _mock
    if _mock is not None:
        return
    real = client.get_client()[client.get_db().name]  # read-only from here on
    mock = mongomock.MongoClient(tz_aware=True)
    copy = mock[real.name]
    for name in real.list_collection_names():
        if not name.startswith("system."):
            docs = list(real[name].find())
            if docs:
                copy[name].insert_many(docs)
    client.ensure_indexes(copy)
    _mock = mock
    client.get_client = lambda: mock

    def no_inbox(*_a, **_k):
        return gmail.InboxResult(False, "Sandbox: inbox checks are off here, so the real inbox and demo data aren't touched.",
                                 error="not_configured")
    gmail.check_inbox = no_inbox
    gmail.start_polling = lambda *_a, **_k: None
    email.send_reminder = lambda approval: f"reminder to {approval.get('to_email')} NOT sent (sandbox)"

    def no_whatsapp(body, to=None):
        print(f"[sandbox] WhatsApp NOT sent: {body[:120]}")
        return whatsapp.Sent(True, "sandbox")
    whatsapp.send = no_whatsapp
    whatsapp.start_polling = lambda *_a, **_k: None
    print(f"CoFoundr sandbox: {sum(copy[n].estimated_document_count() for n in copy.list_collection_names())} documents copied "
          f"into memory from {real.name}; nothing you do locally reaches Atlas.")


def active() -> bool:
    return _mock is not None
