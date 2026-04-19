"""SurrealDB connection and CRUD operations"""

from surrealdb import AsyncSurreal

from core.schema import SovereignEdge, SovereignNode

DB_URL = "ws://localhost:8000/rpc"
DB_NAMESPACE = "sovereign"
DB_DATABASE = "brain"


async def get_db() -> AsyncSurreal:
    db = AsyncSurreal(DB_URL)
    await db.signin({"user": "root", "pass": "sovereign_pass"})
    await db.use(DB_NAMESPACE, DB_DATABASE)
    return db


async def store_node(db: AsyncSurreal, node: SovereignNode) -> dict:
    """Store a validated node into the knowledge graph"""
    result = await db.create("knowledge_node", node.model_dump(mode="json"))
    return result


async def store_edge(db: AsyncSurreal, edge: SovereignEdge) -> dict:
    """Store a relationship between two nodes"""
    result = await db.query(
        "RELATE $from_node->$relation->$to_node SET weight = $weight",
        {
            "from_node": edge.from_node,
            "to_node": edge.to_node,
            "relation": edge.relation,
            "weight": edge.weight,
        },
    )
    return result


async def query_nodes(db: AsyncSurreal, domain: str | None = None) -> list[dict]:
    """Query nodes with optional domain filter"""
    if domain:
        result = await db.query(
            "SELECT * FROM knowledge_node WHERE domain = $domain",
            {"domain": domain},
        )
    else:
        result = await db.query("SELECT * FROM knowledge_node")
    return result
