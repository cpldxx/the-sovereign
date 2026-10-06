"""Sovereign's `web_search` tool for DeerFlow (SearXNG, paced).

Free search engines behind SearXNG rate-limit or CAPTCHA a client that queries too fast. Sovereign
never works around that; it searches less often (MIN_INTERVAL between queries) and, when engines are
suspended, tells the agent to stop searching and work with what it already found.
For heavy use, set TAVILY_API_KEY or BRAVE_API_KEY in research/.env — runner.py then uses those
providers instead.
"""

import asyncio
import json
import os
import threading
import time

import httpx
from langchain.tools import tool

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")
MIN_INTERVAL = float(os.getenv("RESEARCH_SEARCH_INTERVAL", "4"))  # seconds between searches
MAX_RESULTS = 8

# DeerFlow runs parallel tool calls on separate event loops: an asyncio.Lock shared across them deadlocked a run
# that issued three searches at once. Each search reserves its time slot under a thread lock instead.
_next_slot = 0.0
_slot_lock = threading.Lock()


async def search(query: str, time_range: str | None = None) -> tuple[list[dict], list[str]]:
    """(results, unresponsive engines). Never raises."""
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
        for r in data.get("results", [])[:MAX_RESULTS] if r.get("url")
    ]
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
