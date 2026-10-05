"""MCP adapter — the same KG operations as the REST API, exposed as agent tools.

Hermes (or any MCP client) connects to http://localhost:8080/mcp and discovers these tools
with their schemas; no glue code is needed on the agent side. Every tool calls the same
`core` functions the REST routes use.
"""

from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from core import database as kgdb
from core import kg
from core.database import ArcadeDB
from core.ontology import Ontology, save_ontology
from domains import registry

mcp = MCPServer(
    "sovereign-kg",
    instructions=(
        "The Sovereign knowledge graph. Each domain has its own graph of ENTITIES (things) connected by FACTS "
        "(sourced statements whose weight grows as more independent sources confirm them; superseded facts are "
        "kept with valid=false). Ground domain answers in query_knowledge_graph, drill into an entity with "
        "get_entity, persist new information with ingest_data. Cite facts with their weight and sources."
    ),
)

# Set by main.py's lifespan: the same ArcadeDB client the REST routes use.
db: ArcadeDB | None = None


def _require(domain: str) -> dict:
    """Load a domain or fail with a message the agent can act on (plain exceptions reach it unexplained)."""
    try:
        return registry.load_domain(domain)
    except ValueError as e:
        raise ToolError(f"{e}. Known domains: {registry.list_domains()}") from e


@mcp.tool()
def list_domains() -> list[str]:
    """List the knowledge domains (one isolated knowledge graph each)."""
    return registry.list_domains()


@mcp.tool()
async def query_knowledge_graph(domain: str, query: str, k: int = 5) -> dict:
    """Search a domain's knowledge graph by meaning (Graph RAG).

    Returns `entities` (closest ones carry a `similarity`), `facts` (closest first, then the strongest valid
    facts around those entities; each with relation, fact text, weight 0-1, evidence count and source episode
    uids) and `episodes` (the sources behind the facts: URL/name, title, collection status).
    """
    _require(domain)
    return await kg.query(db, domain, query, max(1, min(k, 50)))


@mcp.tool()
async def get_entity(domain: str, uid: str) -> dict:
    """Everything the graph knows about one entity: its summary and aliases, every fact touching it
    (strongest first, superseded ones marked valid=false) and the episodes that mention it."""
    _require(domain)
    detail = await kgdb.entity_detail(db, domain, uid)
    if not detail:
        raise ToolError(f"Entity {uid} not found in {domain}")
    return detail


@mcp.tool()
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


@mcp.tool()
def get_ontology(domain: str) -> dict:
    """The domain's ontology: allowed entity types and relation types (plus the built-in has_state for facts
    about a single entity)."""
    return _require(domain)["ontology"]


@mcp.tool()
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


@mcp.tool()
async def list_reviews(domain: str) -> list[dict]:
    """Pending review items — ambiguous writes waiting for the Head Agent:
    kind "fact" (a supported but low-reliability fact), "merge" (maybe the same entity as an existing one),
    "link" (a new fact may restate or replace existing facts). Each has a summary and its payload."""
    _require(domain)
    return await kgdb.list_reviews(db, domain, "pending")


@mcp.tool()
async def resolve_review(domain: str, uid: str, approve: bool, note: str = "") -> dict:
    """Decide a review item. approve=true applies it (adds the fact / merges the entities / links the facts);
    approve=false dismisses it. Give a short note with your reasoning."""
    _require(domain)
    try:
        return await kg.resolve_review(db, domain, uid, approve, note)
    except ValueError as e:
        raise ToolError(str(e)) from e
