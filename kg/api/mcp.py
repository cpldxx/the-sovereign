"""MCP adapter — the same KG operations as the REST API, exposed as agent tools.

Hermes (or any MCP client) connects and discovers these tools with their schemas; no glue code is needed
on the agent side. Every tool calls the same `core` functions the REST routes use.

Each role gets its own endpoint that serves only that role's tools — what an agent may do is decided by
where it connects, not by its prompt:
    head      /mcp           everything: writes, ontology changes, review decisions, research missions
    readonly  /mcp/readonly  reading the graph, reports and research — for sub-agents and external MCP clients
                             (e.g. a desktop assistant pointed at your graph)

Every call is checked against its caller (auth.py): a person's API token reaches their own domains — /mcp needs
editor there, /mcp/readonly viewer — and Hermes' per-domain Head connections reach only their one domain.
"""

import contextvars
import functools
import inspect
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from core import database as kgdb
from core import activity, auth, charter, crew, digest, exam, kg
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


# Which agent a tool call reaches, for the live agent view (the rest read or write the graph itself).
_TOOL_AGENT = {"ingest_data": "extractor", "list_research": "research", "research_status": "research",
               "daily_report": "reporter", "list_reviews": "validator", "resolve_review": "validator",
               "update_ontology": "ontologist", "get_ontology": "ontologist", "draft_charter": "strategist",
               "suggest_charter": "strategist", "confirm_charter": "research", "exam_results": "learning",
               "take_exam": "learning"}


def _guarded(fn: Callable, need: str) -> Callable:
    """The tool, taking its caller from the HTTP request of each call. (MCP tools run in the session's task, so
    the middleware's contextvar doesn't reach them; the request does.)"""
    @functools.wraps(fn)
    async def call(*args, ctx: Context, **kwargs):
        principal = ctx.request_context.request.scope.get("state", {}).get("principal")
        if principal is None:
            raise ToolError("Not signed in")
        reset = _caller.set((principal, need))
        # Only into a feed the caller may use: before the role check, anyone signed in could write into another
        # tenant's agent view (2026-10-14).
        if isinstance(domain := kwargs.get("domain"), str) and auth.denial(principal, domain, need) is None:
            activity.event(domain, "head" if principal.kind == "service" else "user",
                           _TOOL_AGENT.get(fn.__name__, "graph"), "tool", fn.__name__)
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
async def list_reviews(domain: str, limit: int = 15) -> dict:
    """Pending review items — ambiguous writes waiting for the Head Agent, oldest first, `limit` at a time:
    kind "fact" (a supported but low-reliability fact), "merge" (maybe the same entity as an existing one),
    "link" (a new fact may restate or replace existing facts). Each has a summary and its payload; `pending` is how
    many wait in all."""
    _require(domain)
    # Counted and paged in the database: it read every pending item (up to 10 000) to show 15.
    items = await kgdb.list_reviews(db, domain, "pending", limit=max(1, min(limit, 50)), oldest_first=True)
    return {"pending": await kgdb.count_reviews(db, domain, "pending"), "items": items}


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


# ── The research charter: what the user wants from this domain ─────────────

def _charter_view(state: dict) -> dict:
    return {"status": state["status"], "charter": state["charter"],
            "suggestions": [{k: x[k] for k in ("kind", "text")} for x in state["suggestions"]]}


@tool()
async def draft_charter(domain: str, notes: str) -> dict:
    """Write the research charter from your interview with the user. `notes`: everything they told you that matters,
    in their words — what they want to understand or decide and why, the questions they care about most, what to
    leave out, how deep, which sources they trust. Takes a minute. A first charter is a draft until the user
    agrees (confirm_charter); an active one is revised in place — only when the user asked for the change."""
    _require(domain)
    try:
        return _charter_view(await charter.draft(db, domain, notes))
    except charter.CharterError as e:
        raise ToolError(str(e)) from e


@tool()
async def confirm_charter(domain: str) -> dict:
    """The user agreed to the drafted charter: it becomes active and the domain's first deep research starts from it
    (a broad first study unless the domain has one, then one research run per core question — in the background,
    hours with a local model). Only after the user said yes."""
    _require(domain)
    try:
        state = await charter.start(db, domain)
    except charter.CharterError as e:
        raise ToolError(str(e)) from e
    if state["kickoff"] is not None:
        exam.start(db, domain)   # the baseline
    return {**_charter_view(state), "research": state["kickoff"] or "already active: nothing new started"}


@tool()
async def suggest_charter(domain: str, text: str, kind: Literal["question", "scope", "exclude"] = "question",
                          why: str = "") -> dict:
    """Propose an addition to the charter — when the user keeps asking about something it doesn't cover (a new
    core question, a sub-topic in scope, or something to leave out). The user takes or leaves it in Settings ›
    Charter; tell them you suggested it."""
    _require(domain)
    try:
        return _charter_view(await charter.suggest(db, domain, kind, text, why))
    except charter.CharterError as e:
        raise ToolError(str(e)) from e


@tool("readonly")
async def exam_results(domain: str) -> dict:
    """How much of the research charter the graph can answer: the latest exam score (0-100) and the one before, each
    core question's state (answered / partial / open), and the exam questions still open — where research should go.
    Use it when asked how much the domain knows, how it has grown, or what it still can't answer."""
    _require(domain)
    run, runs = await exam.latest(db, domain), await exam.history(db, domain, 30)
    if not run:
        return {"exam": None, "note": "No exam yet — it is taken after each learning cycle once a charter exists."}
    same = [r for r in runs if r["version"] == run["version"]]
    return {"score": run["score"], "taken_at": run["created_at"], "first_score": same[0]["score"] if same else None,
            "sittings": len(same), "answered": run["answered"], "partial": run["partial"], "open": run["open"],
            "cores": [{k: c[k] for k in ("question", "score", "status")} for c in run["cores"]],
            "open_questions": [x["question"] for x in run["items"] if x["points"] == 0][:10],
            "running": exam.running(domain)}


@tool()
async def take_exam(domain: str) -> dict:
    """Take the exam now, in the background (minutes): every question of the charter's exam answered from the graph
    alone. The result shows in exam_results and in the Briefing › Growth view."""
    _require(domain)
    if not (await charter.get(db, domain))["charter"]:
        raise ToolError("The exam is drawn from the research charter — there is none yet")
    return {"started": exam.start(db, domain), "note": "already running" if exam.running(domain) else ""}


# ── Senses: the world right now (the graph can be a day old) ──────────────

# web_search and read_webpage take no domain: without a check, any signed-in account — even one with no domain —
# could use them as a search / fetch proxy (2026-10-14). They need the service or a role somewhere, and are paced.
PROXY_CALLS = int(os.getenv("MCP_PROXY_CALLS_PER_MINUTE", "30"))
_proxy_calls: dict[str, deque] = defaultdict(deque)


def _proxy_allowed() -> None:
    principal, _ = _caller.get()
    if principal.kind != "service" and not principal.visible(registry.list_domains()):
        raise ToolError("Live search and reading need access to a domain")
    who = f"service:{principal.scope}" if principal.kind == "service" else f"user:{(principal.user or {}).get('uid')}"
    calls, t = _proxy_calls[who], time.monotonic()
    while calls and t - calls[0] > 60:
        calls.popleft()
    if len(calls) >= PROXY_CALLS:
        raise ToolError(f"Too many live searches / page reads ({PROXY_CALLS} a minute) — wait a minute")
    calls.append(t)


@tool("readonly")
async def web_search(query: str, recent: bool = False) -> list[dict]:
    """Search the web right now: titles, URLs, snippets. recent=true prefers the last day's results.
    Nothing is stored — use start_research for knowledge worth keeping."""
    _proxy_allowed()
    params = {"q": query, "limit": 8} | ({"time_range": "day"} if recent else {})
    out = await _research("GET", "/search", params=params, timeout=60)
    return out.get("results", [])


@tool("readonly")
async def read_webpage(url: str) -> dict:
    """Read one public web page right now, as text: it is opened in a real browser, so pages built by JavaScript work
    too (robots.txt respected; sites that refuse automated visitors can't be read — try another site). Use it for
    what is true now: a quote page, an official announcement, a status page. Nothing is stored —
    ingest_data it if it is worth keeping."""
    _proxy_allowed()
    return await _research("POST", "/fetch", json={"url": url, "max_chars": 12000}, timeout=120)


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


# ── The team (core/crew.py) ───────────────────────────────────────────────
# The Head's agents. Work goes out on a queue; each agent reports back on the thread, and the Head is woken to
# read the report. Questions are answered from what the agent knows right now.

ASK_WAIT = 120


@tool("readonly")
def list_agents() -> list[dict]:
    """Your team: every agent, what it does, and the tasks you can assign it with their inputs."""
    return crew.roster()


@tool()
async def assign_task(domain: str, agent: str, task: str, inputs: dict | str | None = None,
                      thread: str | None = None) -> dict:
    """Give a teammate work. `task` is the task's name only (e.g. "check"); its fields go in `inputs` as an object
    (e.g. {"claims": ["…"], "source_text": "…"}) — list_agents shows every task's inputs. It is queued; the agent
    reports on the thread when done and you are woken to read the report. Long tasks (a research mission, a
    relink) take minutes: tell the user it started — don't wait. `thread`: continue an earlier one."""
    _require(domain)
    try:
        return await crew.assign(db, domain, agent, task, inputs, thread)
    except crew.CrewError as e:
        raise ToolError(str(e)) from e


@tool()
async def ask_agent(domain: str, agent: str, question: str, thread: str | None = None) -> dict:
    """Ask a teammate a question — e.g. Learning what it rejected lately or what the graph is unsure about, the
    Researcher which sources it read. It answers from what it knows right now; waits up to 2 minutes, otherwise
    the answer arrives in your inbox. `thread`: a follow-up in an earlier conversation."""
    _require(domain)
    try:
        return await crew.ask(db, domain, agent, question, thread, wait=ASK_WAIT)
    except crew.CrewError as e:
        raise ToolError(str(e)) from e


@tool()
async def inbox(domain: str) -> list[dict]:
    """Unread reports, answers and notices from your team, oldest first (marks them read)."""
    _require(domain)
    return [{k: m.get(k) for k in ("thread", "sender", "kind", "task", "text", "created_at")}
            for m in await crew.inbox(db, domain)]


@tool("readonly")
async def read_thread(domain: str, thread: str) -> dict:
    """One conversation with your team: who asked whom what, and every answer and report, oldest first."""
    _require(domain)
    if not (t := await crew.thread(db, domain, thread)):
        raise ToolError(f"No thread '{thread}' in {domain}")
    return {"thread": t["uid"], "title": t["title"], "agent": t["agent"], "status": t["status"],
            "messages": [{k: m.get(k) for k in ("sender", "recipient", "kind", "task", "text", "status", "created_at")}
                         for m in t["messages"]]}


def _server(role: str) -> MCPServer:
    server = MCPServer(f"sovereign-kg-{role}", instructions=INSTRUCTIONS)
    for fn, roles in _TOOLS:
        if role in roles:
            server.add_tool(_guarded(fn, "editor" if role == "head" else "viewer"), name=fn.__name__)
    return server


# One MCP server per role (main.py serves each at PATHS[role] and runs its session manager).
servers = {role: _server(role) for role in PATHS}
