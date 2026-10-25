"""Local news feeds (RSS) → one plain dict per item. Returns data only: storing it is the middle's job
(backend/news.py). Headline, link, date and a short description only, never the article.

    python -m backend.inputs.news_feeds   # print what the feeds have right now
"""
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import List, Optional

from backend.inputs.fetch import FetchError

FEEDS = [("Hackney Gazette", "https://www.hackneygazette.co.uk/news/rss")]
TIMEOUT = 10
MAX_BYTES = 2 * 1024 * 1024
MAX_SUMMARY = 200


def _text(raw: Optional[str], limit: int) -> str:
    """Plain text: tags stripped, entities decoded, whitespace collapsed, cut at a word."""
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def parse(xml: bytes, source: str) -> List[dict]:
    """RSS 2.0 → [{"source", "url", "headline", "summary", "published_at"}]. Items without an http(s) link, a title
    or a readable date are left out; a feed that isn't RSS raises FetchError."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise FetchError(f"{source} sent something that isn't RSS") from None
    out = []
    for item in root.iter("item"):
        url, title = (item.findtext("link") or "").strip(), _text(item.findtext("title"), 300)
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        if not (title and re.match(r"https?://", url) and when):
            continue
        out.append({"source": source, "url": url, "headline": title, "summary": _text(item.findtext("description"), MAX_SUMMARY),
                    "published_at": (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)})
    return out


def fetch(source: str, url: str, timeout: float = TIMEOUT) -> List[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "CoFoundr/1.0 (hackathon demo)", "Accept": "application/rss+xml, text/xml"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FetchError(f"{source} answered HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"{source} is unreachable ({getattr(e, 'reason', None) or type(e).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise FetchError(f"{source} sent more than {MAX_BYTES // 1024 // 1024} MB")
    return parse(raw, source)


def fetch_all() -> tuple:
    """(items from every feed, [errors]): one broken feed never stops the others."""
    items, errors = [], []
    for source, url in FEEDS:
        try:
            items += fetch(source, url)
        except FetchError as e:
            errors.append(str(e))
    return items, errors


if __name__ == "__main__":
    found, problems = fetch_all()
    for i in found[:20]:
        print(f"{i['published_at']:%a %d %b} | {i['source']} | {i['headline'][:90]}")
    for p in problems:
        print("ERROR", p)
    print(f"{len(found)} item(s), {datetime.now(timezone.utc):%H:%M} UTC")
