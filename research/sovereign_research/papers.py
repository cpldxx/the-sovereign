"""Scholarly literature for the research agent: `paper_search` and `read_paper`, like web_search and web_fetch.

    OpenAlex   every field, with abstracts and open-access links            api.openalex.org (no key)
    arXiv      preprints: computing, physics, maths, quantitative fields    export.arxiv.org (one call per 3 s)
    PubMed     biomedicine                                                  eutils.ncbi.nlm.nih.gov (3 calls a second)

Official APIs made for programs — nothing is scraped, nothing gets around a paywall. A paper the agent reads is
recorded like a page it read, with tier "paper": the knowledge graph learns its abstract (and, when asked, its
open-access full text, fetched under the usual rules) and trusts it as a paper (kg/core/sources.py).
"""

import asyncio
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx
from langchain.tools import tool

from sovereign_research import fetch

OPENALEX = "https://api.openalex.org/works"
ARXIV = "https://export.arxiv.org/api/query"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAILTO = os.getenv("OPENALEX_MAILTO", "").strip()   # optional: OpenAlex's polite pool
HEADERS = {"User-Agent": fetch.USER_AGENT}
ABSTRACT_SHOWN = 300     # characters of an abstract in a search result
FULL_TEXT_CHARS = 60_000
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_pace: dict[str, float] = {}           # API → earliest next call (monotonic)
_PACE = {"arxiv": 3.0, "pubmed": 0.34, "openalex": 0.1}
# DeerFlow runs parallel tool calls on separate event loops (and /papers runs on the server's): an asyncio.Lock shared
# across them binds to one loop and then raises "bound to a different event loop" (fetch.py, search.py). Each call
# reserves its time slot under a thread lock instead.
_lock = threading.Lock()


@dataclass
class Paper:
    key: str                      # "openalex:W…", "arxiv:2401.01234", "pubmed:3812…"
    title: str
    abstract: str
    url: str                      # the DOI when there is one — one paper, one source, whichever API found it
    year: int | None = None
    venue: str = ""
    authors: list[str] = field(default_factory=list)
    citations: int | None = None
    full_text: str = ""           # an open-access page with the full text, when there is one

    def markdown(self) -> str:
        who = ", ".join(self.authors[:6]) + (" et al." if len(self.authors) > 6 else "")
        about = " · ".join(x for x in (who, self.venue, str(self.year or ""),
                                       f"cited by {self.citations}" if self.citations is not None else "") if x)
        return f"# {self.title}\n{about}\n{self.url}\n\n## Abstract\n{self.abstract}"


_found: dict[str, Paper] = {}   # what paper_search returned, for read_paper (the latest 500)


async def _wait(api: str) -> None:
    with _lock:
        now = time.monotonic()
        slot = max(now, _pace.get(api, 0.0))
        _pace[api] = slot + _PACE[api]
    if slot > now:
        await asyncio.sleep(slot - now)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _doi_url(doi: str | None) -> str:
    doi = (doi or "").strip()
    return doi if doi.startswith("http") else f"https://doi.org/{doi}" if doi else ""


# ── The three APIs ─────────────────────────────────────────────────────────

def _abstract(inverted: dict | None) -> str:
    """OpenAlex keeps abstracts as word → positions."""
    if not inverted:
        return ""
    words = sorted((p, w) for w, positions in inverted.items() for p in positions)
    return " ".join(w for _, w in words)


async def openalex(query: str, n: int) -> list[Paper]:
    await _wait("openalex")
    params = {"search": query, "per-page": n, "filter": "has_abstract:true",
              "select": "id,doi,display_name,publication_year,authorships,primary_location,cited_by_count,"
                        "abstract_inverted_index,best_oa_location"}
    if MAILTO:
        params["mailto"] = MAILTO
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as c:
        r = await c.get(OPENALEX, params=params)
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        oa = w.get("best_oa_location") or {}
        page = oa.get("pdf_url") or oa.get("landing_page_url") or ""   # the PDF is the text itself (fetch reads PDFs)
        out.append(Paper(
            key=f"openalex:{w['id'].rsplit('/', 1)[-1]}", title=_clean(w.get("display_name")),
            abstract=_clean(_abstract(w.get("abstract_inverted_index"))),
            url=_doi_url(w.get("doi")) or w["id"], year=w.get("publication_year"),
            venue=_clean(((w.get("primary_location") or {}).get("source") or {}).get("display_name")),
            authors=[_clean((a.get("author") or {}).get("display_name")) for a in w.get("authorships") or []],
            citations=w.get("cited_by_count"),
            full_text=page))
    return [p for p in out if p.title and p.abstract]


async def arxiv(query: str, n: int) -> list[Paper]:
    await _wait("arxiv")
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as c:
        # Every term required: "all:hybrid bonding memory" matched papers with any of them (hybrid Routhian systems).
        terms = re.findall(r"\w+", query)[:8]
        r = await c.get(ARXIV, params={"search_query": " AND ".join(f"all:{t}" for t in terms), "max_results": n,
                                       "sortBy": "relevance"})
    r.raise_for_status()
    out = []
    for e in ET.fromstring(r.text).findall("a:entry", _ATOM):
        abs_url = (e.findtext("a:id", "", _ATOM) or "").strip()
        ident = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
        doi = e.findtext("arxiv:doi", "", _ATOM)
        out.append(Paper(
            key=f"arxiv:{ident}", title=_clean(e.findtext("a:title", "", _ATOM)),
            abstract=_clean(e.findtext("a:summary", "", _ATOM)), url=_doi_url(doi) or f"https://arxiv.org/abs/{ident}",
            year=int((e.findtext("a:published", "", _ATOM) or "0")[:4] or 0) or None, venue="arXiv",
            authors=[_clean(a.findtext("a:name", "", _ATOM)) for a in e.findall("a:author", _ATOM)],
            full_text=f"https://arxiv.org/html/{ident}"))
    return [p for p in out if p.title and p.abstract]


async def pubmed(query: str, n: int) -> list[Paper]:
    common = {"db": "pubmed", "tool": "sovereign"}
    await _wait("pubmed")
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as c:
        r = await c.get(f"{EUTILS}/esearch.fcgi", params={**common, "term": query, "retmax": n, "retmode": "json",
                                                           "sort": "relevance"})
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        await _wait("pubmed")
        r = await c.get(f"{EUTILS}/efetch.fcgi", params={**common, "id": ",".join(ids), "retmode": "xml"})
    r.raise_for_status()
    out = []
    for a in ET.fromstring(r.text).findall(".//PubmedArticle"):
        art = a.find(".//Article")
        if art is None:
            continue
        pmid = a.findtext(".//PMID", "")
        parts = [(t.get("Label") + ": " if t.get("Label") else "") + "".join(t.itertext())
                 for t in art.findall(".//Abstract/AbstractText")]
        # The article's own ids only: every cited reference carries an ArticleIdList too (first read here, a paper got
        # the DOI of one of its references — 2026-10-28).
        ids_ = {i.get("IdType"): (i.text or "").strip() for i in a.findall("PubmedData/ArticleIdList/ArticleId")}
        authors = [_clean(f"{x.findtext('ForeName', '')} {x.findtext('LastName', '')}") for x in art.findall(".//Author")]
        out.append(Paper(
            key=f"pubmed:{pmid}", title=_clean("".join(art.find("ArticleTitle").itertext())
                                              if art.find("ArticleTitle") is not None else ""),
            abstract=_clean(" ".join(parts)), url=_doi_url(ids_.get("doi")) or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            year=int(y) if (y := art.findtext(".//Journal/JournalIssue/PubDate/Year", "")).isdigit() else None,
            venue=_clean(art.findtext(".//Journal/Title", "")), authors=[x for x in authors if x],
            full_text=f"https://pmc.ncbi.nlm.nih.gov/articles/{ids_['pmc']}/" if ids_.get("pmc") else ""))
    return [p for p in out if p.title and p.abstract]


async def search(query: str, n: int = 6) -> list[Paper]:
    """Papers from the three APIs, taken in turn (OpenAlex, PubMed, arXiv), one paper once (by DOI or title). An API
    that fails or has nothing is skipped."""
    found = await asyncio.gather(openalex(query, n), pubmed(query, min(n, 4)), arxiv(query, min(n, 4)),
                                 return_exceptions=True)
    lists = [f for f in found if isinstance(f, list)]
    for f in found:
        if isinstance(f, Exception):
            print(f"[Papers] {type(f).__name__}: {str(f)[:160]}", flush=True)
    seen, out = set(), []
    for i in range(max((len(x) for x in lists), default=0)):
        for papers in lists:
            if i < len(papers):
                p = papers[i]
                ident = p.url.lower() if "doi.org" in p.url else re.sub(r"\W+", "", p.title.lower())
                if ident not in seen:
                    seen.add(ident)
                    out.append(p)
    for p in out:
        _found[p.key] = p
    while len(_found) > 500:
        _found.pop(next(iter(_found)))
    return out[:n]


def brief(p: Paper) -> dict:
    """A search result as the agent sees it."""
    return {"key": p.key, "title": p.title, "year": p.year, "venue": p.venue, "citations": p.citations,
            "url": p.url, "abstract": p.abstract[:ABSTRACT_SHOWN] + ("…" if len(p.abstract) > ABSTRACT_SHOWN else ""),
            "full_text": bool(p.full_text)}


async def read(key: str, full_text: bool = True) -> fetch.Page:
    """A found paper as a page — its abstract, and its open-access full text when there is one and it's readable
    (under fetch's rules: robots.txt, pacing). Recorded for the knowledge graph with tier "paper". An abstract alone
    gave the graph 5–12 facts against 50–90 from a long web page (2026-10-28), so the full text is read unless the
    agent asks for less."""
    p = _found.get(key)
    if p is None:
        return fetch.Page(key, "", "", "failed", "unknown key: search with paper_search first", tier="paper")
    text = p.markdown()
    if full_text and p.full_text:
        page = await fetch.fetch_page(p.full_text)
        if page.status != "failed" and len(page.markdown) > len(p.abstract) * 2:
            text += f"\n\n## Full text ({p.full_text})\n{page.markdown[:FULL_TEXT_CHARS]}"
    page = fetch.Page(p.url, p.title, text, "full", tier="paper")
    fetch.record(page)
    return page


# ── The agent's tools ──────────────────────────────────────────────────────

@tool("paper_search", parse_docstring=True)
async def paper_search_tool(query: str, max_results: int = 6) -> str:
    """Search the scholarly literature: OpenAlex (every field), arXiv (preprints in computing, physics, maths) and
    PubMed (biomedicine). Returns papers with their key, year, venue, citation count and the start of the abstract;
    read the promising ones with read_paper. Reviews and well-cited papers give the established picture, recent ones
    the state of the art.

    Args:
        query: The key terms, in English.
        max_results: How many papers, at most 10.
    """
    import json  # noqa: PLC0415

    papers = await search(query, max(1, min(max_results, 10)))
    if not papers:
        return "No papers found. Try broader or English key terms — or use web_search for non-academic sources."
    had = await fetch.known([p.url for p in papers])
    papers = [p for p in papers if p.url not in had]
    if not papers:
        return "The knowledge graph has every paper found: search other key terms for papers it lacks."
    return json.dumps([brief(p) for p in papers], ensure_ascii=False) + (
        f"\n\n({len(had)} paper(s) the knowledge graph already has were left out.)" if had else "")


@tool("read_paper", parse_docstring=True)
async def read_paper_tool(key: str, full_text: bool = True) -> str:
    """Read a paper found with paper_search — its abstract and details; what it states is learned into the knowledge
    graph, trusted as a paper.

    Args:
        key: The paper's key from paper_search.
        full_text: Read its open-access full text too, when there is one (the default — an abstract holds little);
            false for the abstract alone.
    """
    if (p := _found.get(key)) and await fetch.known([p.url]):
        return f"The knowledge graph already has {p.url}: read a paper it lacks."
    page = await read(key, full_text)
    if page.status == "failed":
        return f"Could not read {key}: {page.error}"
    return page.markdown[:fetch.AGENT_CHARS]
