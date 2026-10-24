"""WhatsApp out, through Twilio: replies to the owner, and the /updates feed pushed to their phone unprompted.

- send() posts one message to Twilio's REST API (stdlib only). Never raises; errors never contain the auth token.
- push_updates() sends whatever /updates has for the "whatsapp" consumer. The cursor only moves past what was
  actually sent, so a Twilio outage delays messages instead of losing them.
- WhatsApp rule (sandbox and live): we can only message the owner within 24 h of their last message to us.
  Outside that window Twilio accepts the request but WhatsApp doesn't deliver it, so the demo starts with the owner
  saying "hi".

Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM (the sandbox number) and OWNER_WHATSAPP.
"""
import base64
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from backend.db import activity
from backend.outputs import updates

API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TIMEOUT = 10
MAX_CHARS = 1600  # Twilio's limit for one WhatsApp message body
CONSUMER = "whatsapp"

_lock = threading.Lock()  # one push at a time
_last_failure: Optional[str] = None  # so a broken token is logged once, not every tick


@dataclass
class Sent:
    ok: bool
    sid: Optional[str] = None  # Twilio's message id
    error: Optional[str] = None  # one line, safe to log (no secrets)


def number(raw: Optional[str]) -> str:
    """'whatsapp:+44 7700 900123' / '+447700900123' → 'whatsapp:+447700900123' (empty if there's no number)."""
    digits = "".join(c for c in (raw or "").removeprefix("whatsapp:") if c.isdigit() or c == "+")
    return f"whatsapp:{digits}" if digits.strip("+") else ""


def _config():
    return (os.environ.get("TWILIO_ACCOUNT_SID", "").strip(), os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
            number(os.environ.get("TWILIO_WHATSAPP_FROM")), number(os.environ.get("OWNER_WHATSAPP")))


def configured() -> bool:
    return all(_config())


def owner() -> str:
    return _config()[3]


def auth_header(sid: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()


def _post(url: str, form: dict, auth: str) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Authorization": auth, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read() or b"{}")


def send(body: str, to: Optional[str] = None) -> Sent:
    """Send one WhatsApp message (to the owner unless `to` is given)."""
    sid, token, sender, own = _config()
    to = number(to) if to else own
    if not (sid and token and sender and to):
        return Sent(False, error="WhatsApp is off: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, OWNER_WHATSAPP")
    if len(body) > MAX_CHARS:
        body = body[:MAX_CHARS - 1] + "…"
    try:
        data = _post(API.format(sid=sid), {"From": sender, "To": to, "Body": body}, auth_header(sid, token))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read(4000) or b"{}").get("message", "")
        except (ValueError, OSError):
            detail = ""
        if "ContentSid" in detail:  # WhatsApp's 24 h rule: only templates until the owner messages us again
            detail = "the owner hasn't messaged the WhatsApp number in the last 24 h (send it \"hi\" first)"
        return Sent(False, error=f"Twilio answered HTTP {e.code}" + (f": {detail[:200]}" if detail else ""))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return Sent(False, error=f"Twilio is unreachable ({type(e).__name__})")
    return Sent(True, sid=data.get("sid"))


def _failed(error: str) -> None:
    global _last_failure
    if error != _last_failure:
        activity.log("whatsapp_send_failed", f"Couldn't send a WhatsApp message: {error}; will retry", error=error)
        _last_failure = error


def push_updates() -> int:
    """Send the owner everything new from /updates. Returns how many messages went out."""
    global _last_failure
    if not configured() or not _lock.acquire(blocking=False):
        return 0
    try:
        data = updates.get_updates(consumer=CONSUMER, peek=True)
        sent, done_until = 0, None
        for i, u in enumerate(data["updates"]):
            result = send(u["message"])
            if not result.ok:
                _failed(result.error)
                break
            sent += 1
            last_of_event = i + 1 == len(data["updates"]) or data["updates"][i + 1]["id"] != u["id"]
            if last_of_event:
                done_until = u["id"]
        else:  # everything went: also skip the events that had nothing to say
            done_until = data["next_since"]
            _last_failure = None
        if done_until:
            updates.advance(CONSUMER, done_until)
        if sent:
            activity.log("whatsapp_pushed", f"Sent the owner {sent} WhatsApp update(s) unprompted", actor="agent", count=sent)
        return sent
    finally:
        _lock.release()


def start_polling(every_seconds: int) -> threading.Event:
    """Push updates every `every_seconds` in a background thread. Set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while not stop.wait(every_seconds):
            try:
                push_updates()
            except Exception as e:  # never let the pusher die quietly
                _failed(f"push crashed ({type(e).__name__})")
    threading.Thread(target=loop, name="whatsapp-pusher", daemon=True).start()
    return stop
