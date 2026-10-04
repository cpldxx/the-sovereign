"""ArcadeDB connection and knowledge graph operations (HTTP API + Cypher).

One ArcadeDB database per domain. Nodes are `Node` vertices; every ontology
relation is its own edge type, so Cypher reads naturally:

    MATCH (a:Node)-[:confirms]->(b:Node) RETURN a, b
"""

import os
import re

import httpx
from dotenv import load_dotenv

from core.schema import SovereignEdge, SovereignNode

load_dotenv()

DB_URL = os.getenv("ARCADEDB_URL", "http://localhost:2480")
DB_USER = os.getenv("ARCADEDB_USER", "root")
DB_PASS = os.getenv("ARCADEDB_PASS", "sovereign_pass")

NODE_TYPE = "Node"

_NON_IDENT = re.compile(r"[^A-Za-z0-9_]+")

# Created once per domain database. The unique uid index makes uid lookups
# (edge creation, MERGE) indexed and rejects duplicate nodes.
_SCHEMA = f"""
CREATE VERTEX TYPE {NODE_TYPE} IF NOT EXISTS;
CREATE PROPERTY {NODE_TYPE}.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON {NODE_TYPE} (uid) UNIQUE;
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
    name = _NON_IDENT.sub("_", relation.strip().lower()).strip("_")
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


async def drop_domain_db(db: ArcadeDB, domain: str) -> None:
    db._ready.discard(domain)
    if await db.exists(domain):
        await db.server(f"drop database {db_name(domain)}")


# ── Writes ─────────────────────────────────────────────────────────────────

async def store_node(db: ArcadeDB, domain: str, node: SovereignNode) -> None:
    """Upsert a validated node (keyed by uid)."""
    await ensure_domain_db(db, domain)
    props = node.model_dump(mode="json", exclude={"uid"})
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

async def query_nodes(db: ArcadeDB, domain: str) -> list[dict]:
    """All nodes of a domain, as plain SovereignNode-shaped dicts."""
    if not await db.exists(domain):
        return []
    rows = await db.cypher(domain, f"MATCH (n:{NODE_TYPE}) RETURN n {{.*}} AS n")
    return [r["n"] for r in rows]


async def query_edges(db: ArcadeDB, domain: str) -> list[dict]:
    """All edges of a domain, as SovereignEdge-shaped dicts."""
    if not await db.exists(domain):
        return []
    return await db.cypher(
        domain,
        f"MATCH (a:{NODE_TYPE})-[r]->(b:{NODE_TYPE}) "
        "RETURN a.uid AS from_node, b.uid AS to_node, type(r) AS relation, r.weight AS weight",
    )
