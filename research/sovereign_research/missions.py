"""What the research agent is asked to do, per mission kind. `brief`: the domain as research sees it — its description,
and the user's research charter once confirmed (goal, core questions, what to leave out, depth; kg/core/charter.py)."""

from collections import Counter
from urllib.parse import urlsplit

BOOTSTRAP_PAGES = 8
UPDATE_PAGES = 6
MISSION_PAGES = 4

_SOURCING = (
    "Prefer primary and authoritative sources (official sites, filings, standards bodies, regulators, reputable "
    "press, papers) and recent ones; skip listicles, ads, SEO pages and forums. If a page cannot be read, find the "
    "same information on another public source — never try to get around a paywall or login."
)


def bootstrap(name: str, brief: str) -> str:
    return f"""You are researching a NEW knowledge domain so it can be mapped into a knowledge graph.
Domain: {name} — {brief}

Build a well-sourced foundation. Search broadly: an overview, the main actors, ideas and methods, how they depend
on each other, recent developments and open debates — and when the domain has core questions, make sure what you
read can answer them. Read at least {BOOTSTRAP_PAGES} substantive pages in full with web_fetch. {_SOURCING}

Then write a short report (under 250 words): the main entities of this domain, how they relate, and the best
sources to keep monitoring (as URLs)."""


def update(name: str, brief: str, known_urls: list[str], days: int = 7) -> str:
    """`known_urls`: the domain's sources, newest first. The lists were alphabetical until 2026-10-14 — the same
    30 old pages and the first 15 site names of the alphabet every time."""
    hosts = Counter(urlsplit(u).netloc.removeprefix("www.") for u in known_urls if u.startswith("http"))
    sites = [h for h, _ in hosts.most_common(15) if h]
    known = "\n".join(f"- {u}" for u in known_urls[:30]) or "- (none yet)"
    return f"""Find what is NEW in this domain over the last {days} days.
Domain: {name} — {brief}

Use web_search with time_range "week" (or "month" if little turns up). Open the {UPDATE_PAGES} most relevant NEW
articles in full with web_fetch. {_SOURCING}
Sites that proved useful before: {", ".join(sites) or "none yet"}.
Already read — do NOT open these again:
{known}

Then write a short report (under 200 words) on what changed, with sources."""


def mission(name: str, brief: str, question: str) -> str:
    return f"""Research mission for the "{name}" domain ({brief}):
{question}

Search, then read the most relevant pages in full with web_fetch (at least {MISSION_PAGES}). {_SOURCING}
Then answer in under 200 words, citing the pages you read."""
