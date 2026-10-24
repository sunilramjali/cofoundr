"""Shared Gemini client. Use `get_client()` and `MODEL` everywhere."""
import os
from functools import lru_cache

from dotenv import load_dotenv
from google import genai

load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-4-argon")


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env (see .env.example).")
    return genai.Client(api_key=key)
