"""ArcadeDB connection and knowledge graph operations (HTTP API + Cypher).

One ArcadeDB database per domain. Nodes are `Node` vertices; every ontology
relation is its own edge type, so Cypher reads naturally:

    MATCH (a:Node)-[:confirms]->(b:Node) RETURN a, b

Nodes may carry an `embedding` (LSM_VECTOR index, cosine) for semantic search.
Embeddings are internal: reads strip them from returned nodes.
"""

import os
import re

import httpx
from dotenv import load_dotenv

from core.ontology import normalize_type
from core.schema import SovereignEdge, SovereignNode

load_dotenv()

DB_URL = os.getenv("ARCADEDB_URL", "http://localhost:2480")
DB_USER = os.getenv("ARCADEDB_USER", "root")
DB_PASS = os.getenv("ARCADEDB_PASS", "sovereign_pass")

NODE_TYPE = "Node"
VECTOR_INDEX = f"{NODE_TYPE}[embedding]"

_NON_IDENT = re.compile(r"[^A-Za-z0-9_]+")

# Created once per domain database. The unique uid index makes uid lookups
# (edge creation, MERGE) indexed and rejects duplicate nodes.
_SCHEMA = f"""
CREATE VERTEX TYPE {NODE_TYPE} IF NOT EXISTS;
CREATE PROPERTY {NODE_TYPE}.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON {NODE_TYPE} (uid) UNIQUE;
"""

# Created on the first embedded write, when the vector dimension is known.
_VECTOR_SCHEMA = """
CREATE PROPERTY {node}.embedding IF NOT EXISTS ARRAY_OF_FLOATS;
CREATE INDEX IF NOT EXISTS ON {node} (embedding) LSM_VECTOR METADATA {{dimensions: {dims}, similarity: 'COSINE'}};
"""


class ArcadeDBError(RuntimeError):
    pass


def db_name(domain: str) -> str:
    """Domain name -> ArcadeDB database name ('Quant trading' -> 'Quant_trading')."""
    name = _NON_IDENT.sub("_", domain.strip()).strip("_")
    if not name:
        raise ValueError(f"Invalid domain name: {domain!r}")
    return name


def edge_type(relation: str) -> str:
    """Relation -> edge type name. Sanitized because it is spliced into Cypher."""
    name = normalize_type(relation)
    if not name or name[0].isdigit():
        raise ValueError(f"Invalid relation type: {relation!r}")
    return name


class ArcadeDB:
    """Thin async client over the ArcadeDB HTTP API."""

    def __init__(self, url: str = DB_URL, user: str = DB_USER, password: str = DB_PASS):
        self._http = httpx.AsyncClient(
            base_url=f"{url}/api/v1", auth=(user, password), timeout=httpx.Timeout(30.0)
        )
        self._ready: set[str] = set()  # domain databases known to exist with schema
        self._vector_ready: set[str] = set()  # domain databases known to have the vector index

    async def close(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        r = await self._http.post(path, json=payload)
        body = r.json() if r.content else {}
        if r.is_error:
            raise ArcadeDBError(f"{r.status_code} {body.get('error', '')}: {body.get('detail', '')}")
        return body

    async def server(self, command: str) -> dict:
        return await self._post("/server", {"command": command})

    async def command(self, domain: str, language: str, query: str, params: dict | None = None) -> list[dict]:
        body = await self._post(
            f"/command/{db_name(domain)}",
            {"language": language, "command": query, "params": params or {}},
        )
        return body.get("result", [])

    async def cypher(self, domain: str, query: str, **params) -> list[dict]:
        return await self.command(domain, "cypher", query, params)

    async def ready(self) -> bool:
        """True when the ArcadeDB server answers its readiness probe."""
        try:
            r = await self._http.get("/ready")
            return r.status_code == 204
        except httpx.HTTPError:
            return False

    async def exists(self, domain: str) -> bool:
        r = await self._http.get(f"/exists/{db_name(domain)}")
        r.raise_for_status()
        return bool(r.json().get("result"))


async def get_db() -> ArcadeDB:
    return ArcadeDB()


# ── Domain databases ───────────────────────────────────────────────────────

async def ensure_domain_db(db: ArcadeDB, domain: str) -> None:
    """Create the domain's database + schema if missing. Idempotent."""
    if domain in db._ready:
        return
    if not await db.exists(domain):
        await db.server(f"create database {db_name(domain)}")
    await db.command(domain, "sqlscript", _SCHEMA)
    db._ready.add(domain)


async def ensure_vector_index(db: ArcadeDB, domain: str, dims: int) -> None:
    """Create the embedding property + vector index if missing. Idempotent."""
    if domain in db._vector_ready:
        return
    await db.command(domain, "sqlscript", _VECTOR_SCHEMA.format(node=NODE_TYPE, dims=dims))
    db._vector_ready.add(domain)


async def drop_domain_db(db: ArcadeDB, domain: str) -> None:
    db._ready.discard(domain)
    db._vector_ready.discard(domain)
    if await db.exists(domain):
        await db.server(f"drop database {db_name(domain)}")


# ── Writes ─────────────────────────────────────────────────────────────────

async def store_node(
    db: ArcadeDB, domain: str, node: SovereignNode, embedding: list[float] | None = None
) -> None:
    """Upsert a validated node (keyed by uid), with its embedding when given."""
    await ensure_domain_db(db, domain)
    props = node.model_dump(mode="json", exclude={"uid"})
    if embedding:
        await ensure_vector_index(db, domain, len(embedding))
        props["embedding"] = embedding
    await db.cypher(
        domain,
        f"MERGE (n:{NODE_TYPE} {{uid: $uid}}) SET n += $props",
        uid=node.uid,
        props=props,
    )


async def store_edge(db: ArcadeDB, domain: str, edge: SovereignEdge) -> bool:
    """Connect two existing nodes. Returns False if an endpoint is missing or the relation is unusable."""
    try:
        relation = edge_type(edge.relation)
    except ValueError:
        return False
    await ensure_domain_db(db, domain)
    rows = await db.cypher(
        domain,
        f"MATCH (a:{NODE_TYPE} {{uid: $from_uid}}), (b:{NODE_TYPE} {{uid: $to_uid}}) "
        f"MERGE (a)-[r:`{relation}`]->(b) SET r.weight = $weight "
        "RETURN count(r) AS n",
        from_uid=edge.from_node,
        to_uid=edge.to_node,
        weight=edge.weight,
    )
    return bool(rows and rows[0].get("n"))


# ── Reads ──────────────────────────────────────────────────────────────────

def _public(node: dict) -> dict:
    """Drop internal fields (the embedding vector) from a node dict."""
    node.pop("embedding", None)
    return node


async def query_nodes(db: ArcadeDB, domain: str) -> list[dict]:
    """All nodes of a domain, as plain SovereignNode-shaped dicts."""
    if not await db.exists(domain):
        return []
    rows = await db.cypher(domain, f"MATCH (n:{NODE_TYPE}) RETURN n {{.*}} AS n")
    return [_public(r["n"]) for r in rows]


async def query_edges(db: ArcadeDB, domain: str) -> list[dict]:
    """All edges of a domain, as SovereignEdge-shaped dicts."""
    if not await db.exists(domain):
        return []
    return await db.cypher(
        domain,
        f"MATCH (a:{NODE_TYPE})-[r]->(b:{NODE_TYPE}) "
        "RETURN a.uid AS from_node, b.uid AS to_node, type(r) AS relation, r.weight AS weight",
    )


async def search_nodes(db: ArcadeDB, domain: str, vector: list[float], k: int = 5) -> list[dict]:
    """The k nodes closest to `vector` (cosine), each with its `distance` (lower = closer)."""
    if not await db.exists(domain):
        return []
    indexes = await db.command(
        domain, "sql", "SELECT name FROM schema:indexes WHERE name = :name", {"name": VECTOR_INDEX}
    )
    if not indexes:  # nothing embedded in this domain yet
        return []
    rows = await db.command(
        domain,
        "sql",
        "SELECT expand(vector.neighbors(:index, :vector, :k))",
        {"index": VECTOR_INDEX, "vector": vector, "k": k},
    )
    fields = SovereignNode.model_fields
    return [{**{f: r.get(f) for f in fields}, "distance": r.get("distance")} for r in rows]


async def neighborhood(db: ArcadeDB, domain: str, uids: list[str]) -> tuple[list[dict], list[dict]]:
    """Edges touching `uids` plus the nodes on their far side (1 hop)."""
    if not uids:
        return [], []
    edges = await db.cypher(
        domain,
        f"MATCH (a:{NODE_TYPE})-[r]->(b:{NODE_TYPE}) WHERE a.uid IN $uids OR b.uid IN $uids "
        "RETURN a.uid AS from_node, b.uid AS to_node, type(r) AS relation, r.weight AS weight",
        uids=uids,
    )
    seeds = set(uids)
    far = sorted({e[end] for e in edges for end in ("from_node", "to_node")} - seeds)
    if not far:
        return [], edges
    rows = await db.cypher(
        domain, f"MATCH (n:{NODE_TYPE}) WHERE n.uid IN $uids RETURN n {{.*}} AS n", uids=far
    )
    return [_public(r["n"]) for r in rows], edges


async def domain_stats(db: ArcadeDB, domain: str) -> dict:
    """Node/edge counts and nodes per category."""
    if not await db.exists(domain):
        return {"node_count": 0, "edge_count": 0, "categories": {}}
    rows = await db.cypher(domain, f"MATCH (n:{NODE_TYPE}) RETURN n.category AS category, count(n) AS n")
    edges = await db.cypher(domain, f"MATCH (:{NODE_TYPE})-[r]->(:{NODE_TYPE}) RETURN count(r) AS n")
    categories = {r["category"]: r["n"] for r in rows}
    return {
        "node_count": sum(categories.values()),
        "edge_count": edges[0]["n"] if edges else 0,
        "categories": categories,
    }
