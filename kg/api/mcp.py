"""MCP adapter — the same KG operations as the REST API, exposed as agent tools.

Hermes (or any MCP client) connects to http://localhost:8080/mcp and discovers
these tools with their schemas; no glue code is needed on the agent side.
Every tool calls the same `core` functions the REST routes use.
"""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from core import kg
from core.database import ArcadeDB
from core.ontology import Ontology, save_ontology
from domains import registry

mcp = MCPServer(
    "sovereign-kg",
    instructions=(
        "The Sovereign knowledge graph. Each domain has its own validated, ontology-constrained KG. "
        "Ground domain answers in query_knowledge_graph before answering, and persist new facts with "
        "ingest_data (it is validated before storage). Cite node uids and reliability."
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
    """Search a domain's knowledge graph by meaning.

    Returns the k closest knowledge nodes (`matches`, with cosine `distance`, lower is closer),
    the nodes one hop away in the graph (`neighbors`) and the relationships between them (`edges`).
    """
    _require(domain)
    return await kg.query(db, domain, query, max(1, min(k, 50)))


@mcp.tool()
async def ingest_data(domain: str, raw_text: str, source: str = "agent") -> dict:
    """Add new knowledge to a domain's graph.

    Runs the full pipeline: extract facts as nodes, keep only ontology-conforming ones,
    validate them, store them, and discover relationships. `source` is where the text came
    from (URL, paper, API). Returns what was stored and what was rejected, with reasons.
    """
    _require(domain)
    if not raw_text.strip():
        raise ToolError("raw_text is empty")
    return await kg.ingest(db, domain, raw_text, source)


@mcp.tool()
def get_ontology(domain: str) -> dict:
    """The domain's ontology: allowed node categories (entity_types) and relationships (relation_types)."""
    return _require(domain)["ontology"]


@mcp.tool()
def update_ontology(domain: str, entity_types: list[str], relation_types: list[str]) -> dict:
    """Replace the domain's ontology. Head Agent only — change it only on evidence from accumulated data.

    Existing nodes keep their categories; the new grammar applies to everything ingested afterwards.
    """
    _require(domain)
    try:
        ontology = Ontology(entity_types=entity_types, relation_types=relation_types)
    except ValidationError as e:
        raise ToolError(f"Invalid ontology: {e}") from e
    save_ontology(domain, ontology)
    return ontology.model_dump()
