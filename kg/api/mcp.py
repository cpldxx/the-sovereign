"""MCP adapter — the same KG operations as the REST API, exposed as agent tools.

Hermes (or any MCP client) connects and discovers these tools with their schemas; no glue code is needed
on the agent side. Every tool calls the same `core` functions the REST routes use.

Each role gets its own endpoint that serves only that role's tools — what an agent may do is decided by
where it connects, not by its prompt:
    head      /mcp           everything: writes, ontology changes, review decisions, action proposals
    readonly  /mcp/readonly  reading the graph, reports, playbooks, actions and sensors — for sub-agents and
                             external MCP clients (e.g. a desktop assistant pointed at your graph)
No role can confirm or execute an action: that is the user's call, through the REST API / UI.

Every call is checked against its caller (auth.py): a person's API token reaches their own domains — /mcp needs
editor there, /mcp/readonly viewer — and Hermes' per-domain Head connections reach only their one domain.
"""

import contextvars
import functools
import inspect
import os
from collections.abc import Callable
from typing import Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from core import database as kgdb
from core import actions, auth, coding, digest, kg, playbooks, sensors
from core.database import ArcadeDB
from core.ontology import Ontology, save_ontology
from domains import registry

INSTRUCTIONS = (
    "The Sovereign knowledge graph. Each domain has its own graph of ENTITIES (things) connected by FACTS "
    "(sourced statements whose weight grows as more independent sources confirm them; superseded facts are "
    "kept with valid=false). Ground domain answers in query_knowledge_graph, drill into an entity with "
    "get_entity, persist new information with ingest_data. Cite facts with their weight and sources."
)
PATHS = {"head": "/mcp", "readonly": "/mcp/readonly"}

_TOOLS: list[tuple[Callable, frozenset[str]]] = []


def tool(*roles: str):
    """Register an agent tool for these roles (the Head always has every tool)."""
    def register(fn: Callable) -> Callable:
        _TOOLS.append((fn, frozenset({"head", *roles})))
        return fn
    return register

# Set by main.py's lifespan: the same ArcadeDB client the REST routes use.
db: ArcadeDB | None = None

# The research service (DeerFlow) — reached through these tools so agents keep one entry point.
RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")


async def _research(method: str, path: str, timeout: float = 30, **kw) -> dict:
    try:
        async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=timeout, headers=auth.headers()) as client:
            r = await client.request(method, path, **kw)
    except httpx.HTTPError as e:
        raise ToolError(f"Research service unreachable at {RESEARCH_URL} ({type(e).__name__})") from e
    if r.is_error:
        raise ToolError(f"Research service: {r.status_code} {r.text[:300]}")
    return r.json()


# The caller of the tool call being handled, and the role this endpoint needs (set by _guarded).
_caller: contextvars.ContextVar[tuple[auth.Principal, str]] = contextvars.ContextVar("mcp_caller")


def _guarded(fn: Callable, need: str) -> Callable:
    """The tool, taking its caller from the HTTP request of each call. (MCP tools run in the session's task, so
    the middleware's contextvar doesn't reach them; the request does.)"""
    @functools.wraps(fn)
    async def call(*args, ctx: Context, **kwargs):
        principal = ctx.request_context.request.scope.get("state", {}).get("principal")
        if principal is None:
            raise ToolError("Not signed in")
        reset = _caller.set((principal, need))
        try:
            result = fn(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        finally:
            _caller.reset(reset)

    sig = inspect.signature(fn)
    call.__signature__ = sig.replace(parameters=[
        *sig.parameters.values(), inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)])
    call.__annotations__ = {**fn.__annotations__, "ctx": Context}
    return call


def _visible() -> list[str]:
    principal, _ = _caller.get()
    return principal.visible(registry.list_domains())


def _require(domain: str) -> dict:
    """Load a domain the caller may use here, or fail with a message the agent can act on (plain exceptions reach
    it unexplained). Other accounts' domains look exactly like missing ones."""
    principal, need = _caller.get()
    if problem := auth.denial(principal, domain, need):
        known = ", ".join(_visible()) or "none"
        raise ToolError(f"{problem[1]}. Your domains: {known}" if problem[0] == 404 else problem[1])
    try:
        return registry.load_domain(domain)
    except ValueError as e:
        raise ToolError(f"{e}. Your domains: {', '.join(_visible()) or 'none'}") from e


@tool("readonly")
def list_domains() -> list[str]:
    """List the knowledge domains you can use (one isolated knowledge graph each)."""
    return _visible()


# What agents get back is compacted: every token of a tool result is re-read on each later model turn, and the
# full records (timestamps, alias lists, per-fact source lists) tripled a query's size for nothing an answer uses.
MAX_FACTS = 25


def _entity(e: dict) -> dict:
    return {"uid": e["uid"], "name": e["name"], "type": e.get("type"), "summary": (e.get("summary") or "")[:240]}


def _fact(f: dict) -> dict:
    out = {"uid": f["uid"], "fact": f["fact"], "relation": f["relation"], "source_uid": f["source_uid"],
           "source": f["source_name"], "target_uid": f["target_uid"], "target": f["target_name"],
           "weight": round(f["weight"], 2), "evidence": f.get("evidence")}
    if not f.get("valid", True):
        out["superseded"] = (f.get("invalid_reason") or "")[:200]
    return out


@tool("readonly")
async def query_knowledge_graph(domain: str, query: str, k: int = 5) -> dict:
    """Search a domain's knowledge graph by meaning (Graph RAG).

    Returns `entities` (the closest first), `facts` (closest first, then the strongest valid facts around those
    entities — at most 25; each with relation, its two entities, weight 0-1 and evidence count) and `sources`
    (the episodes behind the facts: URL/name, title, collection status).
    """
    _require(domain)
    result = await kg.query(db, domain, query, max(1, min(k, 50)))
    return {"entities": [_entity(e) for e in result["entities"][:15]],
            "facts": [_fact(f) for f in result["facts"][:MAX_FACTS]],
            "sources": [{"uid": ep["uid"], "title": ep.get("title") or "", "source": ep["source"],
                         "status": ep.get("content_status")} for ep in result["episodes"][:10]]}


@tool("readonly")
async def get_entity(domain: str, uid: str) -> dict:
    """Everything the graph knows about one entity: its summary and aliases, the facts touching it (strongest
    first, at most 40; superseded ones carry a `superseded` note) and the sources that mention it."""
    _require(domain)
    detail = await kgdb.entity_detail(db, domain, uid)
    if not detail:
        raise ToolError(f"Entity {uid} not found in {domain}")
    facts = detail["facts"]
    return {"entity": {**_entity(detail["entity"]), "aliases": detail["entity"].get("aliases") or [],
                       "mentions": detail["entity"].get("mentions")},
            "facts": [_fact(f) for f in facts[:40]], "more_facts": max(0, len(facts) - 40),
            "sources": [{"uid": ep["uid"], "title": ep.get("title") or "", "source": ep["source"]}
                        for ep in detail["episodes"][:15]]}


@tool()
async def ingest_data(
    domain: str, raw_text: str, source: str = "agent", title: str = "",
    content_status: Literal["full", "partial"] = "full",
) -> dict:
    """Add new information to a domain's graph. The text is stored as an episode; entities and facts are
    extracted, checked against the ontology and the text, merged with what is already known (confirming facts
    get stronger, outdated ones are superseded) and ambiguous items go to the review queue.
    `source`: where the text came from (URL, paper, API). `content_status`: "partial" when only part of the
    source could be read (e.g. paywall) — its facts are trusted less."""
    _require(domain)
    if not raw_text.strip():
        raise ToolError("raw_text is empty")
    report = await kg.ingest(db, domain, raw_text, source, title or None, content_status)
    # The per-item details are long; agents get the counts plus what landed in review.
    return {k: v for k, v in report.items() if k not in ("entities", "facts")} | {
        "review_facts": [f for f in report["facts"] if f["status"] == "review"],
        "rejected_facts": [f for f in report["facts"] if f["status"] == "rejected"][:10],
    }


@tool("readonly")
def get_ontology(domain: str) -> dict:
    """The domain's ontology: allowed entity types and relation types (plus the built-in has_state for facts
    about a single entity)."""
    return _require(domain)["ontology"]


@tool()
def update_ontology(domain: str, entity_types: list[str], relation_types: list[str]) -> dict:
    """Replace the domain's ontology. Head Agent only — change it only on evidence from accumulated data.

    Existing entities and facts keep their types; the new grammar applies to everything ingested afterwards.
    """
    _require(domain)
    try:
        ontology = Ontology(entity_types=entity_types, relation_types=relation_types)
    except ValidationError as e:
        raise ToolError(f"Invalid ontology: {e}") from e
    save_ontology(domain, ontology)
    return ontology.model_dump()


@tool("readonly")
async def list_reviews(domain: str) -> list[dict]:
    """Pending review items — ambiguous writes waiting for the Head Agent:
    kind "fact" (a supported but low-reliability fact), "merge" (maybe the same entity as an existing one),
    "link" (a new fact may restate or replace existing facts). Each has a summary and its payload."""
    _require(domain)
    return await kgdb.list_reviews(db, domain, "pending")


@tool()
async def resolve_review(domain: str, uid: str, approve: bool, note: str = "") -> dict:
    """Decide a review item. approve=true applies it (adds the fact / merges the entities / links the facts);
    approve=false dismisses it. Give a short note with your reasoning."""
    _require(domain)
    try:
        return await kg.resolve_review(db, domain, uid, approve, note)
    except ValueError as e:
        raise ToolError(str(e)) from e


@tool("readonly")
async def daily_report(domain: str) -> dict:
    """The latest daily report for a domain: a headline, a briefing of what changed in the graph (new sources,
    entities and facts, facts confirmed or superseded, research runs, what needs attention) and a short spoken
    version. Reports are written every night after research; use this when asked what happened or what's new."""
    _require(domain)
    reports = await kgdb.list_reports(db, domain, 1)
    if not reports:
        return {"report": None, "generating": digest.running(domain),
                "note": "No report yet — one is written every night after research."}
    return {"report": reports[0], "generating": digest.running(domain)}


@tool("readonly")
async def list_actions(domain: str) -> list[dict]:
    """What this domain can do: built-in actions (alert, draft, research — they run at once) and the user's
    external actions (webhooks — they run only after the user confirms). Each with its parameters."""
    _require(domain)
    return await actions.catalog(db, domain)


@tool("readonly")
async def list_playbooks(domain: str) -> list[dict]:
    """The domain's playbooks: situations worth acting on, each with the entities it watches, the prepared
    response and action, and the facts behind it. Written every night from the graph; a triggered playbook
    becomes a proposal by itself. Check them when deciding what to do."""
    _require(domain)
    return await playbooks.list_playbooks(db, domain)


@tool()
async def propose_action(domain: str, action: str, params: dict, rationale: str,
                         evidence: list[str] | None = None) -> dict:
    """Propose an action from the catalog (list_actions). `rationale`: why, grounded in the graph — cite facts
    with their weight; `evidence`: the uids of those facts. Internal actions run at once; external actions
    are recorded with a dry-run preview and wait for the user's confirmation in the Actions tab — never say
    one was done unless its status is "executed"."""
    _require(domain)
    try:
        return await actions.propose(db, domain, action, params, rationale, evidence, source="head")
    except actions.ActionError as e:
        raise ToolError(str(e)) from e


@tool("readonly")
async def list_proposals(domain: str, status: str = "proposed") -> list[dict]:
    """Proposed actions. status: "proposed" (waiting for the user), "executed", "rejected", "failed", "expired"."""
    _require(domain)
    return await actions.list_proposals(db, domain, status, 30)


# ── Senses: the world right now (the graph can be a day old) ──────────────

@tool("readonly")
async def web_search(query: str, recent: bool = False) -> list[dict]:
    """Search the web right now: titles, URLs, snippets. recent=true prefers the last day's results.
    Nothing is stored — use start_research for knowledge worth keeping."""
    params = {"q": query, "limit": 8} | ({"time_range": "day"} if recent else {})
    out = await _research("GET", "/search", params=params, timeout=60)
    return out.get("results", [])


@tool("readonly")
async def read_webpage(url: str) -> dict:
    """Read one public web page right now, as text: it is opened in a real browser, so pages built by JavaScript work
    too (robots.txt respected; sites that refuse automated visitors can't be read — try another site). Use it for
    live data no sensor covers: a quote page, an official announcement, a status page. Nothing is stored —
    ingest_data it if it is worth keeping."""
    return await _research("POST", "/fetch", json={"url": url, "max_chars": 12000}, timeout=120)


@tool("readonly")
async def list_sensors(domain: str) -> list[dict]:
    """The domain's live-data sensors: name, what it returns, its parameters, and how many independent sources
    (APIs and web pages) back it — reading one falls back from source to source by itself."""
    _require(domain)
    return [{"name": g["name"], "description": g["description"], "params": g["params"],
             "sources": f"{g['working']} working of {len(g['sources'])}", "last_run_at": g["last_run_at"]}
            for g in await sensors.groups(db, domain)]


@tool("readonly")
async def read_sensor(domain: str, name: str, params: dict | None = None, verify_field: str | None = None) -> dict:
    """Read a live-data sensor now (a few seconds; its best working source answers, the next one if that fails).
    Check live data right before acting. `verify_field`: also read a second source and compare that field — do it
    before acting on a number."""
    _require(domain)
    try:
        out = await sensors.read(db, domain, name, params or {}, verify_field)
    except sensors.SensorError as e:
        raise ToolError(str(e)) from e
    if out.get("ok"):
        out["result"] = sensors.clip(out["result"])
    return out


@tool()
def request_sensor(domain: str, need: str) -> dict:
    """Ask for a new sensor for a recurring live-data need (e.g. "latest stock price and day change for a ticker").
    The Scout tries 20-30 public sources (APIs and web pages), keeps every one that works and agrees with the others,
    and the sensor reads them in turn; takes 10-20 minutes. Returns a request id; list_sensors shows it once it
    works."""
    _require(domain)
    j = coding.submit(db, domain, need)
    return {"request": j["id"], "status": j["status"], "need": j["need"]}


@tool()
async def start_research(domain: str, question: str) -> dict:
    """Send the research agent (DeerFlow) on a mission: it searches the web, reads the most relevant pages
    in full and every page it reads is ingested into the domain's graph as a source episode.
    Runs in the background (several minutes). Returns a job id for research_status."""
    _require(domain)
    return await _research("POST", f"/domains/{domain}/research", json={"mode": "mission", "question": question})


@tool("readonly")
async def research_status(job_id: str) -> dict:
    """Status of a research job: queued / researching / ingesting / done / failed, the pages it read
    (with what each added to the graph or why it failed) and, when done, its short report."""
    job = await _research("GET", f"/jobs/{job_id}")
    principal, _ = _caller.get()
    if not principal.can(job.get("domain", "")):
        raise ToolError(f"Research service: 404 Job {job_id} not found")
    for page in job.get("pages", []):
        page.pop("markdown", None)
    return job


@tool("readonly")
async def list_research(domain: str) -> list[dict]:
    """Recent research jobs for a domain (newest first): bootstrap, nightly updates and missions."""
    _require(domain)
    jobs = await _research("GET", "/jobs", params={"domain": domain, "limit": 20})
    return [{k: j.get(k) for k in ("id", "mode", "question", "status", "created_at", "finished_at", "summary")}
            for j in jobs.get("jobs", [])]


def _server(role: str) -> MCPServer:
    server = MCPServer(f"sovereign-kg-{role}", instructions=INSTRUCTIONS)
    for fn, roles in _TOOLS:
        if role in roles:
            server.add_tool(_guarded(fn, "editor" if role == "head" else "viewer"), name=fn.__name__)
    return server


# One MCP server per role (main.py serves each at PATHS[role] and runs its session manager).
servers = {role: _server(role) for role in PATHS}
