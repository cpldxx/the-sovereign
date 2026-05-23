"""SurrealDB connection and CRUD operations"""

import os

from dotenv import load_dotenv
from surrealdb import AsyncSurreal

from core.schema import SovereignEdge, SovereignNode

load_dotenv()

DB_URL = os.getenv("SURREAL_URL", "ws://localhost:8000/rpc")
DB_NAMESPACE = os.getenv("SURREAL_NS", "sovereign")
DB_DATABASE = os.getenv("SURREAL_DB", "brain")
DB_USER = os.getenv("SURREAL_USER", "root")
DB_PASS = os.getenv("SURREAL_PASS", "sovereign_pass")


async def get_db() -> AsyncSurreal:
    db = AsyncSurreal(DB_URL)
    await db.signin({"username": DB_USER, "password": DB_PASS})
    await db.use(DB_NAMESPACE, DB_DATABASE)
    return db


async def store_node(db: AsyncSurreal, node: SovereignNode) -> dict:
    """Store a validated node into the knowledge graph"""
    result = await db.create("knowledge_node", node.model_dump(mode="json"))
    return result


async def store_edge(db: AsyncSurreal, edge: SovereignEdge) -> dict:
    """Store a relationship between two nodes"""
    result = await db.create("knowledge_edge", edge.model_dump(mode="json"))
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


async def query_edges(db: AsyncSurreal, domain: str | None = None) -> list[dict]:
    """Query all edges from knowledge_edge table"""
    if domain:
        # from_node uid starts with domain name
        result = await db.query(
            "SELECT * FROM knowledge_edge WHERE string::starts_with(from_node, $prefix)",
            {"prefix": domain + ":"},
        )
    else:
        result = await db.query("SELECT * FROM knowledge_edge")
    return result
