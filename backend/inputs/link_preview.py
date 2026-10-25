"""Read ONE link the owner added to their news: its title and description, like a link preview. Never a crawl, never a
search of a platform. Returns data only: storing it is the middle's job (backend/news.py).

- TikTok: TikTok's official oEmbed endpoint (title + creator). TikTok's search/research APIs aren't open to businesses.
- Instagram: posts need Meta's API (app review + a business account), so the owner's own note is used with the link.
- Any other page: only if its robots.txt lets us read it; then <title> / Open Graph title + description.
- Safety: http(s) only, ports 80/443, and every address (including each redirect) must be public: never localhost, the
  private network or cloud metadata (no server-side request forgery). Size and time are capped.
"""
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html import unescape
from typing import Optional

AGENT = "CoFoundr/1.0 (hackathon demo; reads one owner-added link)"
TIMEOUT = 6
MAX_BYTES = 1024 * 1024
MAX_URL = 2000
MAX_TITLE, MAX_SUMMARY = 300, 200
TIKTOK_OEMBED = "https://www.tiktok.com/oembed"


class LinkRefused(ValueError):
    """Why a link wasn't read, in words for the owner."""


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def check_url(url: str, resolve=None) -> str:
    """The url, if it's safe to fetch: http(s), a normal port, and a host whose every address is public."""
    resolve = resolve or socket.getaddrinfo
    url = (url or "").strip()
    if len(url) > MAX_URL or not re.match(r"https?://[^\s<>\"']+$", url):
        raise LinkRefused("That isn't a web link (it should start with https://).")
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password:
        raise LinkRefused("Links with a username or password in them aren't read.")
    if parts.port not in (None, 80, 443):
        raise LinkRefused("Only normal web links are read (no unusual ports).")
    host = parts.hostname or ""
    try:
        addresses = {info[4][0] for info in resolve(host, parts.port or (443 if parts.scheme == "https" else 80))}
    except (socket.gaierror, UnicodeError, ValueError):
        raise LinkRefused("That website couldn't be found.") from None
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise LinkRefused("That link points inside a private network, so it isn't read.")
    return url


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)  # every hop must be a public address too
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, accept: str) -> bytes:
    check_url(url)
    opener = urllib.request.build_opener(_SafeRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except LinkRefused:
        raise
    except urllib.error.HTTPError as e:
        raise LinkRefused(f"The page answered with an error ({e.code}).") from None
    except (urllib.error.URLError, OSError):
        raise LinkRefused("The page couldn't be reached right now.") from None
    if len(raw) > MAX_BYTES:
        raise LinkRefused("That page is too big to read.")
    return raw


def _allowed(url: str) -> bool:
    """robots.txt says we may read this page (a missing robots.txt means yes; an unreadable one means no)."""
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        raw = _get(robots_url, "text/plain")
    except LinkRefused as e:
        return "(404)" in str(e) or "(410)" in str(e)
    rules = urllib.robotparser.RobotFileParser()
    rules.parse(raw.decode("utf-8", "replace").splitlines())
    return rules.can_fetch(AGENT, url)


def _clean(text: Optional[str], limit: int) -> str:
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _meta(page: str, *names: str) -> str:
    for name in names:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(name), page, re.I) \
            or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(name), page, re.I)
        if m and m.group(1).strip():
            return m.group(1)
    return ""


def parse_page(html: str) -> dict:
    """Title + description + site name from a page's <title> and Open Graph tags."""
    title = _meta(html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = m.group(1) if m else ""
    return {"headline": _clean(title, MAX_TITLE), "summary": _clean(_meta(html, "og:description", "description", "twitter:description"), MAX_SUMMARY),
            "site": _clean(_meta(html, "og:site_name"), 60)}


def read(url: str, note: str = "") -> dict:
    """{"url", "headline", "summary", "source"} for one owner-added link, or LinkRefused with a reason to show."""
    url = check_url(url)
    host = _host(url)
    note = _clean(note, MAX_SUMMARY)
    if _is(host, "instagram.com"):
        if not note:
            raise LinkRefused("Instagram posts can only be read through Meta's API, so add a short note about it with the link.")
        return {"url": url, "headline": note[:120], "summary": note, "source": "Owner's link · Instagram"}
    if _is(host, "tiktok.com"):
        try:
            data = json.loads(_get(f"{TIKTOK_OEMBED}?{urllib.parse.urlencode({'url': url})}", "application/json"))
        except (LinkRefused, ValueError):  # throttled (429), unknown video, or not JSON: the owner's note still works
            if note:
                return {"url": url, "headline": note[:120], "summary": note, "source": "Owner's link · TikTok"}
            raise LinkRefused("TikTok didn't answer for that video right now: try again later, or add a short note with the link.") from None
        title = _clean(data.get("title"), MAX_TITLE) or note
        if not title:
            raise LinkRefused("TikTok didn't give a title for that video: add a short note.")
        by = _clean(data.get("author_name"), 60)
        return {"url": url, "headline": title, "summary": note or (f"TikTok video by {by}" if by else "TikTok video"),
                "source": "Owner's link · TikTok"}
    if not _allowed(url):
        raise LinkRefused("That website asks tools like ours not to read its pages (robots.txt), so it wasn't read.")
    page = parse_page(_get(url, "text/html").decode("utf-8", "replace"))
    if not page["headline"] and not note:
        raise LinkRefused("That page has no title I could read: add a short note.")
    return {"url": url, "headline": page["headline"] or note[:120], "summary": note or page["summary"],
            "source": f"Owner's link · {page['site'] or host.removeprefix('www.')}"}
