"""Shared Gemini client. Use `get_client()` and `MODEL` everywhere, and wrap calls in `with_retry`."""
import os
import random
import time
from functools import lru_cache
from typing import Callable, TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import errors

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-argon")
RETRYABLE = {429, 500, 502, 503, 504}  # busy / over the per-minute quota: worth another go
DELAYS = (2, 5)  # seconds before the 2nd and 3rd attempt: ~7 s worst case, short enough for a live demo
_sleep = time.sleep  # tests swap this out

T = TypeVar("T")


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env (see .env.example).")
    return genai.Client(api_key=key)


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
