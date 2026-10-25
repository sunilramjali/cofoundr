"""Shared Gemini client. Use `get_client()` and `MODEL` everywhere, wrap calls in `with_retry`, and give every call
`thinking_config=thinking(HIGH or MEDIUM)`. Measured live on Sun 25 Oct:
- HIGH for text-only reasoning that runs in the background: forecast adjustment (18 s), self-grading lessons (14 s),
  ranking priorities (26 s).
- MEDIUM where the owner waits or an image is read: invoice extraction (4–5 s), reminder drafts, the assistant (7 s),
  the fee summary (8 s; HIGH misread it once and took 53 s), and the board meeting (34 s for 176k tokens; HIGH ran
  9 minutes and failed)."""
import os
import random
import time
from functools import lru_cache
from typing import Callable, TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-argon")
RETRYABLE = {429, 500, 502, 503, 504}  # busy / over the per-minute quota: worth another go
HIGH, MEDIUM = types.ThinkingLevel.HIGH, types.ThinkingLevel.MEDIUM
DELAYS = (2, 5)  # seconds before the 2nd and 3rd attempt: ~7 s worst case, short enough for a live demo
_sleep = time.sleep  # tests swap this out

T = TypeVar("T")


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env (see .env.example).")
    return genai.Client(api_key=key)


def thinking(level: types.ThinkingLevel) -> types.ThinkingConfig | None:
    """Argon's thinking level for one call. GEMINI_THINKING=off drops it (the model's default), without a code change."""
    if os.environ.get("GEMINI_THINKING", "").lower() == "off":
        return None
    return types.ThinkingConfig(thinking_level=level)


def with_retry(call: Callable[[], T]) -> T:
    """Run a Gemini call, retrying 429/5xx with backoff. Other errors, or the last failure, are raised
    so the caller can log it and tell the owner (never swallowed here)."""
    for delay in (*DELAYS, None):
        try:
            return call()
        except errors.APIError as e:
            if e.code not in RETRYABLE or delay is None:
                raise
            _sleep(delay + random.uniform(0, 0.5))
