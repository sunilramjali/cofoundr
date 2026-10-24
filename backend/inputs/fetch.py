"""JSON over HTTP for input channels (stdlib only, no extra dependency).

Errors never include the URL: its query string can hold an API key. Every failure is a FetchError with a
plain-English message, so callers can log it and tell the owner instead of crashing.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

TIMEOUT = 10  # seconds
SECRET_PARAMS = ("apikey", "api_key", "app_key", "key", "token")


class FetchError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status  # HTTP status when the service answered, else None


def _reason(err: urllib.error.HTTPError) -> str:
    """The service's own error text, if it sent one (Open-Meteo: reason; Ticketmaster: fault / errors)."""
    try:
        body = json.loads(err.read(4000) or b"{}")
    except (ValueError, OSError):
        return ""
    if not isinstance(body, dict):
        return ""
    text = body.get("reason") or (body.get("fault") or {}).get("faultstring") or ((body.get("errors") or [{}])[0] or {}).get("detail")
    return f": {str(text)[:200]}" if text else ""


def get_json(url: str, params: dict, source: str, timeout: float = TIMEOUT, expect: type = dict):
    """GET url?params → the decoded JSON object (or list, with expect=list), or FetchError("<source> …")."""
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "CoFoundr/1.0 (hackathon demo)", "Accept": "application/json"})
    secrets = [str(v) for k, v in params.items() if k.lower() in SECRET_PARAMS and v]
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        message, status = f"{source} answered HTTP {e.code}{_reason(e)}", e.code
    except (urllib.error.URLError, OSError) as e:  # includes timeouts
        message, status = f"{source} is unreachable ({getattr(e, 'reason', None) or type(e).__name__})", None
    else:
        try:
            data = json.loads(raw)
        except ValueError:
            raise FetchError(f"{source} sent something that isn't JSON") from None
        if not isinstance(data, expect):
            raise FetchError(f"{source} sent JSON that isn't {'a list' if expect is list else 'an object'}")
        return data
    for secret in secrets:
        message = message.replace(secret, "***")
    raise FetchError(message, status) from None
