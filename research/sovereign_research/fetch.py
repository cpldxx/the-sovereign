"""Sovereign's `web_fetch` tool for DeerFlow.

Replaces DeerFlow's built-in fetchers so that every page the research agent opens:
- respects robots.txt (cached per site) and is paced per site,
- is rendered by the local crawl4ai server (JavaScript pages too) into clean markdown,
- is recorded IN FULL for the knowledge graph, while the agent itself gets a trimmed copy.

Nothing is bypassed: blocked, paywalled or login-only pages are recorded as failed or partial,
and the agent is told to find the information elsewhere.
"""

import asyncio
import os
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from deerflow.community.url_safety import validate_public_http_url
from langchain.tools import tool

CRAWL4AI_URL = os.getenv("CRAWL4AI_URL", "http://localhost:11235").rstrip("/")
CRAWL4AI_TOKEN = os.getenv("CRAWL4AI_API_TOKEN", "sovereign-local-crawl4ai")
USER_AGENT = "SovereignBot/0.1 (research agent; respects robots.txt)"
AGENT_CHARS = 12_000      # what the agent reads; the KG gets the whole page
MIN_SITE_INTERVAL = 2.0   # seconds between requests to the same site
PARTIAL_BELOW = 800       # characters: less than this is probably a teaser, not the article

_PAYWALL = re.compile(
    r"subscribe to (continue|read)|to continue reading|sign in to (read|continue)|"
    r"this (article|content) is (only )?(available )?(for|to) (subscribers|members)|"
    r"already a subscriber|create a free account to (read|continue)",
    re.I,
)


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    status: str          # "full" | "partial" | "failed"
    error: str = ""


@dataclass
class Capture:
    """Pages opened during one research run (one run at a time: they share the local GPU)."""
    pages: list[Page] = field(default_factory=list)

    def add(self, page: Page) -> None:
        if not any(p.url == page.url and p.status != "failed" for p in self.pages):
            self.pages.append(page)


current: Capture | None = None   # set by the runner around a research run

_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
# Per-site pacing. DeerFlow runs parallel tool calls on separate event loops, so the pacing state is guarded by
# a thread lock and each call reserves its time slot instead of holding a lock across the wait (an asyncio.Lock
# shared across loops deadlocked a run that fetched three pages at once).
_next_slot: dict[str, float] = {}
_slot_lock = threading.Lock()


def _record(page: Page) -> None:
    if current is not None:
        current.add(page)


async def _robots_allows(url: str) -> bool:
    parts = urlsplit(url)
    site = f"{parts.scheme}://{parts.netloc}"
    if site not in _robots:
        parser = None
        try:
            async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
                r = await c.get(f"{site}/robots.txt")
            if r.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(r.text.splitlines())
        except httpx.HTTPError:
            parser = None  # unreachable robots.txt: no rules published
        _robots[site] = parser
    parser = _robots[site]
    return parser is None or parser.can_fetch(USER_AGENT, url)


async def _pace(url: str) -> None:
    site = urlsplit(url).netloc
    with _slot_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(site, 0.0))
        _next_slot[site] = slot + MIN_SITE_INTERVAL
    if slot > now:
        await asyncio.sleep(slot - now)


def _title(markdown: str) -> str:
    m = re.search(r"^#{1,2}\s+(.+)$", markdown, re.M)
    return m.group(1).strip()[:200] if m else ""


async def fetch_page(url: str) -> Page:
    """Fetch one page under Sovereign's rules. Never raises."""
    if url_error := await asyncio.to_thread(validate_public_http_url, url, allow_private_addresses=False):
        return Page(url, "", "", "failed", url_error)
    if not await _robots_allows(url):
        return Page(url, "", "", "failed", "disallowed by robots.txt")
    await _pace(url)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{CRAWL4AI_URL}/md",
                json={"url": url, "f": "fit"},
                headers={"Authorization": f"Bearer {CRAWL4AI_TOKEN}"},
            )
        body = r.json() if r.content else {}
        markdown = (body.get("markdown") or "").strip() if r.is_success and body.get("success", True) else ""
        if not r.is_success:
            return Page(url, "", "", "failed", f"crawler HTTP {r.status_code}: {str(body.get('detail', ''))[:200]}")
    except (httpx.HTTPError, ValueError) as e:
        return Page(url, "", "", "failed", f"{type(e).__name__}: {e}")
    if not markdown:
        return Page(url, "", "", "failed", "no readable content")
    partial = len(markdown) < PARTIAL_BELOW or bool(_PAYWALL.search(markdown[:5000]))
    return Page(url, _title(markdown), markdown, "partial" if partial else "full")


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL.

    Only fetch EXACT URLs that have been returned by web_search or found in fetched pages.
    Pages that block automated access, require a login or a subscription cannot be read; when that
    happens, look for the same information from another public source.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.

    Args:
        url: The URL to fetch the contents of.
    """
    page = await fetch_page(url)
    _record(page)
    if page.status == "failed":
        return f"Error: could not read {url} ({page.error}). Use another source for this information."
    text = page.markdown
    note = "\n\n[Note: only part of this page was readable (likely paywall/teaser).]" if page.status == "partial" else ""
    if len(text) > AGENT_CHARS:
        text = text[:AGENT_CHARS] + "\n\n[…page truncated for reading; the full page was saved]"
    return text + note
