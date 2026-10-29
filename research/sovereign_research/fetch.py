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
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from deerflow.community.url_safety import validate_public_http_url
from langchain.tools import tool

from sovereign_research import auth

CRAWL4AI_URL = os.getenv("CRAWL4AI_URL", "http://localhost:11235").rstrip("/")
CRAWL4AI_TOKEN = os.getenv("CRAWL4AI_API_TOKEN", "sovereign-local-crawl4ai")
USER_AGENT = "SovereignBot/0.1 (research agent; respects robots.txt)"
AGENT_CHARS = 12_000      # what the agent reads; the KG gets the whole page
MIN_SITE_INTERVAL = 2.0   # seconds between requests to the same site
PARTIAL_BELOW = 800       # characters: less than this is probably a teaser, not the article
# A subscriber phrase marks a page partial only when little text came with it: a full article under a "Subscribe to
# continue reading" banner was marked partial, and every fact of a partial source goes to Head review (2026-10-14).
PAYWALL_BODY = 3000
# robots.txt answers are kept a day; an error (5xx, unreachable, a bad redirect) only briefly — it means "keep out
# for now", not "no rules" (RFC 9309 §2.3.1.4).
ROBOTS_TTL = 24 * 3600
ROBOTS_ERROR_TTL = 600
ROBOTS_REDIRECTS = 5

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
    tier: str = ""       # what kind of source, when it is known here ("paper": read through papers.py)


@dataclass
class Capture:
    """Pages opened during one research run (one run at a time: they share the local GPU) — and, for its `domain`,
    the URLs found that the graph already has."""
    pages: list[Page] = field(default_factory=list)
    domain: str = ""
    known: set[str] = field(default_factory=set)
    checked: set[str] = field(default_factory=set)

    def add(self, page: Page) -> None:
        url = urldefrag(page.url).url        # …/ep2/ and …/ep2/#main-content are one page
        if not any(urldefrag(p.url).url == url and p.status != "failed" for p in self.pages):
            self.pages.append(page)


current: Capture | None = None   # set by the runner around a research run

# site → (expires, rules): a parser, True / False for "everything allowed / disallowed", or None for "robots.txt
# couldn't be read just now" (kept out briefly).
_robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | bool | None]] = {}
# Per-site pacing. DeerFlow runs parallel tool calls on separate event loops, so the pacing state is guarded by
# a thread lock and each call reserves its time slot instead of holding a lock across the wait (an asyncio.Lock
# shared across loops deadlocked a run that fetched three pages at once).
_next_slot: dict[str, float] = {}
_slot_lock = threading.Lock()


def record(page: Page) -> None:
    if current is not None:
        current.add(page)


async def known(urls: list[str]) -> set[str]:
    """Those of `urls` the run's graph has read in full or holds out (the exam's and the practice set's sources): the
    agent isn't shown them. Four missions read 26 pages, 10 of them pages the graph had (2026-10-28) — each a read
    and a page of the run spent for nothing, as ingest skips them. Empty outside a run, or when the KG can't say."""
    cap = current
    if cap is None or not cap.domain:
        return set()
    ask = [u for u in dict.fromkeys(urldefrag(u).url for u in urls if u) if u not in cap.checked][:500]
    if ask:
        try:
            async with httpx.AsyncClient(base_url=auth.KG_URL, headers=auth.headers(cap.domain), timeout=30) as kg:
                r = await kg.post(f"/domains/{cap.domain}/sources", json={"urls": ask})
            cap.known.update(r.raise_for_status().json().get("known", []))
            cap.checked.update(ask)
        except (httpx.HTTPError, ValueError) as e:
            print(f"[Research] known sources unavailable ({type(e).__name__})", flush=True)
    return {u for u in urls if u and urldefrag(u).url in cap.known}


async def _robots_rules(site: str) -> tuple[urllib.robotparser.RobotFileParser | bool | None, float]:
    """(rules, seconds to keep them) for a site. Until 2026-10-14 any answer but 200 — a 401/403, a 5xx, a dropped
    connection — counted as "no rules" and was cached for the process lifetime. Now: 401/403 and errors keep out
    (as the sandbox runner does), 404/410 and other 4xx allow everything. Redirects are followed by hand, each target
    checked like any page (an open redirect must not lead into the local network)."""
    url = f"{site}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as c:
            for _ in range(ROBOTS_REDIRECTS + 1):
                r = await c.get(url)
                if not r.is_redirect:
                    break
                url = urljoin(url, r.headers.get("location", ""))
                if await asyncio.to_thread(validate_public_http_url, url, allow_private_addresses=False):
                    return None, ROBOTS_ERROR_TTL
            else:
                return None, ROBOTS_ERROR_TTL       # too many redirects
    except httpx.HTTPError:
        return None, ROBOTS_ERROR_TTL               # unreachable: keep out for now
    if r.status_code == 200:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(r.text.splitlines())
        return parser, ROBOTS_TTL
    if r.status_code in (401, 403):
        return False, ROBOTS_TTL
    if r.status_code == 429 or r.status_code >= 500:
        return None, ROBOTS_ERROR_TTL
    return True, ROBOTS_TTL                         # 404, 410 …: no rules published


async def _robots_allows(url: str) -> bool:
    return not await robots_refusal(url)


async def robots_refusal(url: str) -> str:
    """Why robots.txt keeps us out of this URL, or "" if it doesn't. A robots.txt that couldn't be read just now is
    worded without "robots" — a 503 for ten minutes is not a refusal."""
    parts = urlsplit(url)
    site = f"{parts.scheme}://{parts.netloc}"
    hit = _robots.get(site)
    if not hit or hit[0] < time.monotonic():
        rules, ttl = await _robots_rules(site)
        if len(_robots) > 5000:
            _robots.clear()
        hit = _robots[site] = (time.monotonic() + ttl, rules)
    rules = hit[1]
    if rules is None:
        return "the site's crawl rules couldn't be read just now — try again in a few minutes"
    allowed = rules if isinstance(rules, bool) else rules.can_fetch(USER_AGENT, url)
    return "" if allowed else "disallowed by robots.txt"


async def _pace(url: str) -> None:
    site = urlsplit(url).netloc
    with _slot_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(site, 0.0))
        _next_slot[site] = slot + MIN_SITE_INTERVAL
    if slot > now:
        await asyncio.sleep(slot - now)


def _text_chars(markdown: str) -> int:
    """Characters of text in a page, without link targets, images and markup."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return len(re.sub(r"[#*_>`|\-\s]+", " ", text).strip())


def _title(markdown: str) -> str:
    m = re.search(r"^#{1,2}\s+(.+)$", markdown, re.M)
    return m.group(1).strip()[:200] if m else ""


async def fetch_page(url: str) -> Page:
    """Fetch one page under Sovereign's rules. Never raises."""
    if url_error := await asyncio.to_thread(validate_public_http_url, url, allow_private_addresses=False):
        return Page(url, "", "", "failed", url_error)
    if refusal := await robots_refusal(url):
        return Page(url, "", "", "failed", refusal)
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
    partial = len(markdown) < PARTIAL_BELOW or (bool(_PAYWALL.search(markdown[:5000]))
                                                 and _text_chars(markdown) < PAYWALL_BODY)
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
    if await known([url]):
        return f"The knowledge graph already has {url} (or keeps it out): don't read it — find a source it lacks."
    page = await fetch_page(url)
    record(page)
    if page.status == "failed":
        return f"Error: could not read {url} ({page.error}). Use another source for this information."
    text = page.markdown
    note = "\n\n[Note: only part of this page was readable (likely paywall/teaser).]" if page.status == "partial" else ""
    if len(text) > AGENT_CHARS:
        text = text[:AGENT_CHARS] + "\n\n[…page truncated for reading; the full page was saved]"
    return text + note
