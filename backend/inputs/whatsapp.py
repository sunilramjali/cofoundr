"""WhatsApp in, through Twilio: what the owner sends CoFoundr on WhatsApp (replaces Saint).

Twilio POSTs each incoming message to /whatsapp/twilio. The bridge checks Twilio's signature (signature_ok) and
answers at once; handle() then runs in the background, because Gemini can take longer than Twilio waits.

- Only the owner's number (OWNER_WHATSAPP) is acted on; anyone else is logged and ignored.
- A photo/PDF → pipeline.ingest_invoice(source="whatsapp_photo"); the reply is the pipeline's own message.
- Exactly "approve 4" / "reject 2" → approvals.decide(via="whatsapp"), never via Gemini.
- Anything else → the Gemini assistant (read-only), whose answer is the reply.
"""
import base64
import hashlib
import hmac
import urllib.error
import urllib.request
from typing import Iterable, List, Optional, Tuple

from backend import approvals
from backend.db import activity
from backend.inputs.replies import parse_reply
from backend.outputs import assistant, whatsapp
from backend.pipeline import MAX_BYTES, ingest_invoice

MAX_MEDIA = 3  # photos handled per message
TIMEOUT = 20


def signature_ok(url: str, params: Iterable[Tuple[str, str]], signature: Optional[str], token: str) -> bool:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(auth token, url + every POST key+value, sorted by key))."""
    if not (signature and token):
        return False
    payload = url + "".join(k + v for k, v in sorted(params, key=lambda kv: kv[0]))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None  # handled below: the storage link it points to must not get our Twilio credentials


def download(url: str) -> Tuple[bytes, str]:
    """A photo the owner sent: Twilio's media URL (needs our credentials) → its storage link (doesn't)."""
    sid, token = whatsapp._config()[:2]
    if not url.startswith("https://api.twilio.com/"):
        raise ValueError("not a Twilio media URL")  # never send our credentials anywhere else
    request = urllib.request.Request(url, headers={"Authorization": whatsapp.auth_header(sid, token)})
    try:
        response = urllib.request.build_opener(_NoRedirect).open(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
            raise
        response = urllib.request.urlopen(e.headers["Location"], timeout=TIMEOUT)
    with response:
        data = response.read(MAX_BYTES + 1)
        mime = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    return data, mime


def _photos(params: dict) -> List[str]:
    replies = []
    count = min(int(params.get("NumMedia") or 0), MAX_MEDIA)
    for i in range(count):
        try:
            data, mime = download(params.get(f"MediaUrl{i}", ""))
        except (urllib.error.URLError, OSError, ValueError) as e:
            activity.log("ingest_failed", f"Couldn't fetch a WhatsApp photo from Twilio ({type(e).__name__})",
                         source="whatsapp_photo", error="download_failed")
            replies.append("I couldn't download that photo. Could you send it again?")
            continue
        result = ingest_invoice(data, mime or params.get(f"MediaContentType{i}", ""), source="whatsapp_photo",
                                origin="WhatsApp photo from the owner")
        replies.append(result.message)
    return replies


def _decide(decision: str, ref: int) -> str:
    approval = approvals.find(ref=ref)
    if not approval:
        return f"There's no #{ref} waiting for you."
    try:
        result = approvals.decide(approval, decision, via="whatsapp")
    except approvals.DecisionError as e:
        return str(e)
    emoji = "✅" if result["status"] == "approved" else "❌"
    return f"{emoji} #{result['ref']} {result['status']}: {result['title']} ({result['effect']})"


def handle(params: dict) -> Optional[str]:
    """One incoming WhatsApp message → the reply to send back (None = say nothing)."""
    sender = whatsapp.number(params.get("From"))
    text = (params.get("Body") or "").strip()
    if not sender or sender != whatsapp.owner():
        activity.log("whatsapp_ignored", f"Ignored a WhatsApp from an unknown number (…{sender[-4:]})", actor="agent")
        return None
    activity.log("whatsapp_received", f"WhatsApp from the owner: “{text[:80]}”" if text else "WhatsApp photo from the owner",
                 actor="owner", media=int(params.get("NumMedia") or 0))
    if int(params.get("NumMedia") or 0) > 0:
        return "\n\n".join(_photos(params))  # a caption on a photo isn't a command
    if not text:
        return None
    parsed = parse_reply(text)
    if parsed:
        return _decide(*parsed)
    return assistant.answer(text).text
