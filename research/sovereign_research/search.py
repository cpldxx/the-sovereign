"""Sovereign's `web_search` tool for DeerFlow (SearXNG, paced).

With TAVILY_API_KEY or BRAVE_API_KEY set, that API answers instead (the agents' live /search too).
Free search engines behind SearXNG rate-limit or CAPTCHA a client that queries too fast. Sovereign
never works around that; it searches less often (MIN_INTERVAL between queries) and, when engines are
suspended, tells the agent to stop searching and work with what it already found.
For heavy use, set TAVILY_API_KEY or BRAVE_API_KEY in research/.env — runner.py then uses those
providers instead.
"""

import asyncio
import json
import os
import re
import threading
import time

import httpx
from langchain.tools import tool

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")
MIN_INTERVAL = float(os.getenv("RESEARCH_SEARCH_INTERVAL", "4"))  # seconds between searches
MAX_RESULTS = 8

# DeerFlow runs parallel tool calls on separate event loops: an asyncio.Lock shared across them deadlocked a run
# that issued three searches at once. Each search reserves its time slot under a thread lock instead.
_STOP = {"the", "and", "for", "with", "from", "that", "this", "what", "when", "how", "are", "was", "not", "no", "its",
         "into", "about", "latest", "new", "news", "free", "best", "top", "use", "using", "www", "http", "https", "com"}
_WORD = re.compile(r"[a-z0-9][a-z0-9.+-]*[a-z0-9]|[a-z0-9]")


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in _STOP}


def relevant(query: str, result: dict) -> bool:
    """Drop results that share (almost) nothing with the query — free engines sometimes answer a blocked or
    misunderstood query with generic pages ("Free Online Games at Poki" for a stock-API question)."""
    terms = _terms(query)
    if not terms:
        return True
    hit = terms & _terms(f"{result['title']} {result['snippet']} {result['url']}")
    return len(hit) >= min(2, len(terms))


_next_slot = 0.0
_slot_lock = threading.Lock()


_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


async def _api_search(query: str, time_range: str | None) -> list[dict] | None:
    """Tavily or Brave when their key is set (far more reliable than free engines); None without a key or on
    failure — then SearXNG answers."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            if key := os.getenv("TAVILY_API_KEY"):
                body = {"query": query, "max_results": MAX_RESULTS}
                if time_range in _BRAVE_FRESHNESS:
                    body["time_range"] = time_range
                r = await c.post("https://api.tavily.com/search", json=body, headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")}
                        for x in r.json().get("results", []) if x.get("url")]
            if key := os.getenv("BRAVE_API_KEY"):
                params = {"q": query, "count": MAX_RESULTS}
                if time_range in _BRAVE_FRESHNESS:
                    params["freshness"] = _BRAVE_FRESHNESS[time_range]
                r = await c.get("https://api.search.brave.com/res/v1/web/search", params=params,
                                headers={"Accept": "application/json", "X-Subscription-Token": key})
                r.raise_for_status()
                return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
                        for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    except (httpx.HTTPError, ValueError) as e:
        print(f"[Research] search API failed, using SearXNG: {type(e).__name__}", flush=True)
    return None


async def search(query: str, time_range: str | None = None) -> tuple[list[dict], list[str]]:
    """(results, unresponsive engines): Tavily / Brave when a key is set, else the local SearXNG. Never raises."""
    if (found := await _api_search(query, time_range)) is not None:
        return found[:MAX_RESULTS], []
    global _next_slot
    with _slot_lock:
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + MIN_INTERVAL
    if slot > now:
        await asyncio.sleep(slot - now)
    params = {"q": query, "format": "json"}
    if time_range in ("day", "week", "month", "year"):
        params["time_range"] = time_range
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            data = (await c.get(f"{SEARXNG_URL}/search", params=params)).json()
    except (httpx.HTTPError, ValueError) as e:
        return [], [f"searxng ({type(e).__name__})"]
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", []) if r.get("url")
    ]
    results = [r for r in results if relevant(query, r)][:MAX_RESULTS]
    return results, [u[0] for u in data.get("unresponsive_engines", [])]


@tool("web_search", parse_docstring=True)
async def web_search_tool(query: str, time_range: str | None = None) -> str:
    """Search the web. Returns titles, URLs and snippets; open promising URLs with web_fetch.

    Searches are rate-limited: prefer a few well-formed queries over many small variations.

    Args:
        query: The search query.
        time_range: Optional recency filter: "day", "week", "month" or "year".
    """
    results, down = await search(query, time_range)
    if not results:
        return (
            "No results. " + (f"Search engines are temporarily limiting requests ({', '.join(down)}). " if down else "")
            + "Do not retry many variations: use a broader query once, or work with the pages you already have."
        )
    return json.dumps(results, ensure_ascii=False)
