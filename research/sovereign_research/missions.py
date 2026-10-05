"""What the research agent is asked to do, per mission kind."""

from urllib.parse import urlsplit

BOOTSTRAP_PAGES = 8
UPDATE_PAGES = 6
MISSION_PAGES = 4

_SOURCING = (
    "Prefer primary and authoritative sources (official sites, filings, standards bodies, regulators, reputable "
    "press, papers) and recent ones; skip listicles, ads, SEO pages and forums. If a page cannot be read, find the "
    "same information on another public source — never try to get around a paywall or login."
)


def bootstrap(name: str, description: str) -> str:
    return f"""You are researching a NEW knowledge domain so it can be mapped into a knowledge graph.
Domain: {name} — {description}

Build a well-sourced foundation. Search broadly: overview, key players, products and technologies, how they
depend on each other, recent developments, and regulation where relevant. Read at least {BOOTSTRAP_PAGES} substantive
pages in full with web_fetch. {_SOURCING}

Then write a short report (under 250 words): the main entities of this domain, how they relate, and the best
sources to keep monitoring (as URLs)."""


def update(name: str, description: str, known_urls: list[str], days: int = 7) -> str:
    sites = sorted({urlsplit(u).netloc for u in known_urls if u.startswith("http")})[:15]
    known = "\n".join(f"- {u}" for u in known_urls[:30]) or "- (none yet)"
    return f"""Find what is NEW in this domain over the last {days} days.
Domain: {name} — {description}

Use web_search with time_range "week" (or "month" if little turns up). Open the {UPDATE_PAGES} most relevant NEW
articles in full with web_fetch. {_SOURCING}
Sites that proved useful before: {", ".join(sites) or "none yet"}.
Already read — do NOT open these again:
{known}

Then write a short report (under 200 words) on what changed, with sources."""


def mission(name: str, description: str, question: str) -> str:
    return f"""Research mission for the "{name}" domain ({description}):
{question}

Search, then read the most relevant pages in full with web_fetch (at least {MISSION_PAGES}). {_SOURCING}
Then answer in under 200 words, citing the pages you read."""
