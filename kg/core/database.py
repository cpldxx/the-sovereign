"""ArcadeDB connection and the neuron knowledge graph (HTTP API + Cypher/SQL).

One ArcadeDB database per domain:

    (:Entity)    neuron   uid, name, type, summary, aliases, keys, embedding, mentions, created_at, updated_at
    -[:<rel>]->  synapse  one edge type per ontology relation, all EXTENDS Fact:
                          uid, fact, sources, evidence, disbelief, embedding, created_at, updated_at,
                          invalid_at, invalid_reason
    (:Episode)   memory   uid, source, title, content, content_status, content_hash, created_at
    (:Episode)-[:MENTIONS]->(:Entity)
    ReviewItem (document) uid, kind, status, summary, payload (JSON), created_at, resolved_at, resolution

A synapse's strength is a noisy-OR over its independent sources:
    weight = 1 - disbelief,  disbelief = Π (1 - reliability of each distinct source)
so every new source that confirms a fact makes it stronger, and one weak source never makes it strong.
Superseded facts are not deleted: `invalid_at` is set, and they stay visible as history.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from core.ontology import normalize_type

load_dotenv()

DB_URL = os.getenv("ARCADEDB_URL", "http://localhost:2480")
DB_USER = os.getenv("ARCADEDB_USER", "root")
DB_PASS = os.getenv("ARCADEDB_PASS", "sovereign_pass")

ENTITY_INDEX = "Entity[embedding]"
STATE = "has_state"  # built-in relation for facts about a single entity (self-loop)
# Relation names that would collide with the schema's own types.
_RESERVED = {"entity", "episode", "fact", "mentions", "reviewitem"}

_NON_IDENT = re.compile(r"[^A-Za-z0-9_]+")

_SCHEMA = """
CREATE VERTEX TYPE Entity IF NOT EXISTS;
CREATE PROPERTY Entity.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Entity (uid) UNIQUE;
CREATE PROPERTY Entity.keys IF NOT EXISTS LIST OF STRING;
CREATE INDEX IF NOT EXISTS ON Entity (keys BY ITEM) NOTUNIQUE;
CREATE VERTEX TYPE Episode IF NOT EXISTS;
CREATE PROPERTY Episode.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Episode (uid) UNIQUE;
CREATE PROPERTY Episode.content_hash IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Episode (content_hash) UNIQUE;
CREATE EDGE TYPE Fact IF NOT EXISTS;
CREATE EDGE TYPE MENTIONS IF NOT EXISTS;
CREATE DOCUMENT TYPE ReviewItem IF NOT EXISTS;
CREATE PROPERTY ReviewItem.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON ReviewItem (uid) UNIQUE;
"""

# Vector indexes need the embedding dimension, so they are created on first use.
# The index on the Fact supertype does not cover its subtypes, so every relation gets its own.
_ENTITY_VECTOR = """
CREATE PROPERTY Entity.embedding IF NOT EXISTS ARRAY_OF_FLOATS;
CREATE INDEX IF NOT EXISTS ON Entity (embedding) LSM_VECTOR METADATA {{dimensions: {dims}, similarity: 'COSINE'}};
"""
_RELATION = """
CREATE EDGE TYPE `{rel}` IF NOT EXISTS EXTENDS Fact;
CREATE PROPERTY `{rel}`.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON `{rel}` (uid) UNIQUE;
CREATE PROPERTY `{rel}`.embedding IF NOT EXISTS ARRAY_OF_FLOATS;
CREATE INDEX IF NOT EXISTS ON `{rel}` (embedding) LSM_VECTOR METADATA {{dimensions: {dims}, similarity: 'COSINE'}};
"""

# Columns returned for a fact everywhere (a = source entity, r = edge, b = target entity).
_FACT_COLUMNS = (
    "r.uid AS uid, type(r) AS relation, r.fact AS fact, a.uid AS source_uid, a.name AS source_name, "
    "b.uid AS target_uid, b.name AS target_name, r.evidence AS evidence, r.disbelief AS disbelief, "
    "r.sources AS sources, r.created_at AS created_at, r.updated_at AS updated_at, "
    "r.invalid_at AS invalid_at, r.invalid_reason AS invalid_reason"
)
_ENTITY_COLUMNS = (
    "e.uid AS uid, e.name AS name, e.type AS type, e.summary AS summary, e.aliases AS aliases, "
    "e.mentions AS mentions, e.created_at AS created_at, e.updated_at AS updated_at"
)


class ArcadeDBError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def db_name(domain: str) -> str:
    """Domain id -> ArcadeDB database name (ids are already identifiers; this is defense in depth)."""
    name = _NON_IDENT.sub("_", domain.strip()).strip("_")
    if not name:
        raise ValueError(f"Invalid domain name: {domain!r}")
    return name


def edge_type(relation: str) -> str:
    """Relation -> edge type name. Sanitized because it is spliced into Cypher."""
    name = normalize_type(relation)
    if not name or name[0].isdigit():
        raise ValueError(f"Invalid relation type: {relation!r}")
    return f"rel_{name}" if name in _RESERVED else name


def _fact(row: dict) -> dict:
    """Edge row -> API fact: weight from disbelief, `valid` flag."""
    row = dict(row)
    disbelief = row.pop("disbelief", None)
    row["weight"] = round(1.0 - (1.0 if disbelief is None else disbelief), 4)
    row["valid"] = row.get("invalid_at") is None
    return row


class ArcadeDB:
    """Thin async client over the ArcadeDB HTTP API."""

    def __init__(self, url: str = DB_URL, user: str = DB_USER, password: str = DB_PASS):
        self._http = httpx.AsyncClient(
            base_url=f"{url}/api/v1", auth=(user, password), timeout=httpx.Timeout(30.0)
        )
        self._ready: set[str] = set()  # domain databases known to exist with schema
        self._indexed: set[tuple[str, str]] = set()  # (domain, type) with a vector index

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

    async def sql(self, domain: str, query: str, **params) -> list[dict]:
        return await self.command(domain, "sql", query, params)

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


# ── Domain databases + schema ──────────────────────────────────────────────

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
    db._indexed = {k for k in db._indexed if k[0] != domain}
    if await db.exists(domain):
        await db.server(f"drop database {db_name(domain)}")


async def _ensure_entity_index(db: ArcadeDB, domain: str, dims: int) -> None:
    if (domain, "Entity") not in db._indexed:
        await db.command(domain, "sqlscript", _ENTITY_VECTOR.format(dims=dims))
        db._indexed.add((domain, "Entity"))


async def _ensure_relation(db: ArcadeDB, domain: str, rel: str, dims: int) -> None:
    if (domain, rel) not in db._indexed:
        await db.command(domain, "sqlscript", _RELATION.format(rel=rel, dims=dims))
        db._indexed.add((domain, rel))


async def _relation_types(db: ArcadeDB, domain: str) -> list[str]:
    """Relation edge types that have a fact vector index."""
    rows = await db.sql(domain, "SELECT name FROM schema:indexes WHERE name LIKE '%[embedding]'")
    return [r["name"].removesuffix("[embedding]") for r in rows if r["name"] != ENTITY_INDEX]


# ── Episodes ───────────────────────────────────────────────────────────────

async def find_episode(db: ArcadeDB, domain: str, content_hash: str) -> dict | None:
    rows = await db.sql(domain, "SELECT uid, source, created_at FROM Episode WHERE content_hash = :h", h=content_hash)
    return rows[0] if rows else None


async def create_episode(db: ArcadeDB, domain: str, episode: dict) -> None:
    await db.cypher(domain, "CREATE (e:Episode) SET e += $props", props=episode)


async def link_mentions(db: ArcadeDB, domain: str, episode_uid: str, entity_uids: list[str]) -> None:
    if entity_uids:
        await db.cypher(
            domain,
            "MATCH (ep:Episode {uid: $ep}), (e:Entity) WHERE e.uid IN $uids MERGE (ep)-[:MENTIONS]->(e)",
            ep=episode_uid,
            uids=entity_uids,
        )


async def list_episodes(db: ArcadeDB, domain: str, limit: int = 50) -> list[dict]:
    if not await db.exists(domain):
        return []
    return await db.cypher(
        domain,
        "MATCH (ep:Episode) OPTIONAL MATCH (ep)-[:MENTIONS]->(e:Entity) "
        "RETURN ep.uid AS uid, ep.source AS source, ep.title AS title, ep.content_status AS content_status, "
        "ep.created_at AS created_at, left(ep.content, 240) AS snippet, count(e) AS entities "
        "ORDER BY created_at DESC LIMIT $n",
        n=limit,
    )


# ── Entities ───────────────────────────────────────────────────────────────

async def entities_by_keys(db: ArcadeDB, domain: str, keys: list[str]) -> list[dict]:
    """Entities owning any of these name keys (exact resolution)."""
    if not keys:
        return []
    return await db.sql(
        domain, "SELECT uid, name, type, keys, summary FROM Entity WHERE keys CONTAINSANY :keys", keys=keys
    )


async def entity_candidates(db: ArcadeDB, domain: str, vector: list[float], k: int = 5) -> list[dict]:
    """Nearest entities by embedding, each with `similarity` (cosine, higher = closer)."""
    if (domain, "Entity") not in db._indexed:
        rows = await db.sql(domain, "SELECT name FROM schema:indexes WHERE name = :n", n=ENTITY_INDEX)
        if not rows:
            return []
        db._indexed.add((domain, "Entity"))
    rows = await db.sql(
        domain, "SELECT expand(vector.neighbors(:index, :v, :k))", index=ENTITY_INDEX, v=vector, k=k
    )
    return [
        {"uid": r["uid"], "name": r["name"], "type": r.get("type"), "summary": r.get("summary") or "",
         "similarity": round(1 - r["distance"], 4)}
        for r in rows
    ]


async def create_entity(db: ArcadeDB, domain: str, entity: dict, embedding: list[float] | None) -> None:
    props = dict(entity)
    if embedding:
        await _ensure_entity_index(db, domain, len(embedding))
        props["embedding"] = embedding
    await db.cypher(domain, "CREATE (e:Entity) SET e += $props", props=props)


async def touch_entity(db: ArcadeDB, domain: str, uid: str, keys: list[str], aliases: list[str], summary: str) -> None:
    """A known entity was mentioned again: add new name keys/aliases, count the mention,
    fill an empty summary."""
    await db.cypher(
        domain,
        "MATCH (e:Entity {uid: $uid}) "
        "SET e.keys = e.keys + [k IN $keys WHERE NOT k IN e.keys], "
        "e.aliases = coalesce(e.aliases, []) + [a IN $aliases WHERE NOT a IN coalesce(e.aliases, []) AND a <> e.name], "
        "e.mentions = coalesce(e.mentions, 0) + 1, e.updated_at = $now, "
        "e.summary = CASE WHEN coalesce(e.summary, '') = '' THEN $summary ELSE e.summary END",
        uid=uid, keys=keys, aliases=aliases, summary=summary, now=now(),
    )


# ── Facts (synapses) ───────────────────────────────────────────────────────

async def create_fact(
    db: ArcadeDB, domain: str, source_uid: str, relation: str, target_uid: str,
    fact: str, embedding: list[float] | None, episode_uid: str, reliability: float,
) -> str:
    rel = edge_type(relation)
    if embedding:
        await _ensure_relation(db, domain, rel, len(embedding))
    else:
        await db.sql(domain, f"CREATE EDGE TYPE `{rel}` IF NOT EXISTS EXTENDS Fact")
    uid = new_uid("f")
    ts = now()
    props = {
        "uid": uid, "fact": fact, "sources": [episode_uid], "evidence": 1,
        "disbelief": round(1.0 - reliability, 6), "created_at": ts, "updated_at": ts,
    }
    if embedding:
        props["embedding"] = embedding
    await db.cypher(
        domain,
        f"MATCH (a:Entity {{uid: $a}}), (b:Entity {{uid: $b}}) CREATE (a)-[r:`{rel}`]->(b) SET r += $props",
        a=source_uid, b=target_uid, props=props,
    )
    return uid


async def strengthen_fact(db: ArcadeDB, domain: str, relation: str, fact_uid: str, episode_uid: str,
                          reliability: float) -> dict:
    """Another source confirms a fact: one more piece of evidence (counted once per source).
    A re-confirmed fact that had been superseded becomes valid again."""
    rows = await db.cypher(
        domain,
        f"MATCH ()-[r:`{edge_type(relation)}`]->() WHERE r.uid = $uid "
        "SET r.evidence = CASE WHEN $src IN r.sources THEN r.evidence ELSE r.evidence + 1 END, "
        "r.disbelief = CASE WHEN $src IN r.sources THEN r.disbelief ELSE r.disbelief * (1 - $rel) END, "
        "r.sources = CASE WHEN $src IN r.sources THEN r.sources ELSE r.sources + $src END, "
        "r.updated_at = $now, r.invalid_at = null, r.invalid_reason = null "
        "RETURN r.evidence AS evidence, r.disbelief AS disbelief",
        uid=fact_uid, src=episode_uid, rel=reliability, now=now(),
    )
    return {"evidence": rows[0]["evidence"], "weight": round(1 - rows[0]["disbelief"], 4)} if rows else {}


async def invalidate_fact(db: ArcadeDB, domain: str, relation: str, fact_uid: str, reason: str) -> None:
    await db.cypher(
        domain,
        f"MATCH ()-[r:`{edge_type(relation)}`]->() WHERE r.uid = $uid SET r.invalid_at = $now, r.invalid_reason = $why",
        uid=fact_uid, now=now(), why=reason,
    )


async def fact_candidates(db: ArcadeDB, domain: str, source_uid: str, relation: str, target_uid: str) -> list[dict]:
    """Existing valid facts a new fact could restate or contradict: any fact between the same two
    entities, or the same subject with the same relation (e.g. an older value of the same state)."""
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NULL AND ("
        "(a.uid = $s AND b.uid = $t) OR (a.uid = $t AND b.uid = $s) OR (a.uid = $s AND type(r) = $rel)) "
        f"RETURN {_FACT_COLUMNS} ORDER BY r.disbelief ASC LIMIT 8",
        s=source_uid, t=target_uid, rel=edge_type(relation),
    )
    return [_fact(r) for r in rows]


async def get_facts(db: ArcadeDB, domain: str, uids: list[str]) -> list[dict]:
    if not uids:
        return []
    rows = await db.cypher(
        domain, f"MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.uid IN $uids RETURN {_FACT_COLUMNS}", uids=uids
    )
    return [_fact(r) for r in rows]


async def merge_entities(db: ArcadeDB, domain: str, duplicate_uid: str, into_uid: str) -> None:
    """Fold a duplicate entity into another: names, mentions and every fact move over."""
    dup = await entity_detail(db, domain, duplicate_uid)
    if not dup:
        return
    await db.cypher(
        domain,
        "MATCH (d:Entity {uid: $d}), (t:Entity {uid: $t}) "
        "SET t.keys = t.keys + [k IN d.keys WHERE NOT k IN t.keys], "
        "t.aliases = coalesce(t.aliases, []) + [a IN (coalesce(d.aliases, []) + d.name) "
        "WHERE NOT a IN coalesce(t.aliases, []) AND a <> t.name], "
        "t.mentions = coalesce(t.mentions, 0) + coalesce(d.mentions, 0), t.updated_at = $now",
        d=duplicate_uid, t=into_uid, now=now(),
    )
    await db.cypher(
        domain,
        "MATCH (ep:Episode)-[:MENTIONS]->(d:Entity {uid: $d}) MATCH (t:Entity {uid: $t}) MERGE (ep)-[:MENTIONS]->(t)",
        d=duplicate_uid, t=into_uid,
    )
    for f in dup["facts"]:
        source = into_uid if f["source_uid"] == duplicate_uid else f["source_uid"]
        target = into_uid if f["target_uid"] == duplicate_uid else f["target_uid"]
        props = {k: f[k] for k in ("fact", "sources", "evidence", "created_at", "updated_at",
                                    "invalid_at", "invalid_reason") if f.get(k) is not None}
        props.update(uid=new_uid("f"), disbelief=round(1 - f["weight"], 6))
        await db.cypher(
            domain,
            f"MATCH (a:Entity {{uid: $a}}), (b:Entity {{uid: $b}}) "
            f"CREATE (a)-[r:`{edge_type(f['relation'])}`]->(b) SET r += $props",
            a=source, b=target, props=props,
        )
    await db.cypher(domain, "MATCH (d:Entity {uid: $d}) DETACH DELETE d", d=duplicate_uid)


# ── Reads ──────────────────────────────────────────────────────────────────

async def graph(db: ArcadeDB, domain: str) -> dict:
    """All entities and facts (including superseded ones, marked valid=false)."""
    if not await db.exists(domain):
        return {"entities": [], "facts": []}
    entities = await db.cypher(domain, f"MATCH (e:Entity) RETURN {_ENTITY_COLUMNS}")
    facts = await db.cypher(domain, f"MATCH (a:Entity)-[r:Fact]->(b:Entity) RETURN {_FACT_COLUMNS}")
    return {"entities": entities, "facts": [_fact(f) for f in facts]}


async def entity_detail(db: ArcadeDB, domain: str, uid: str) -> dict | None:
    rows = await db.cypher(domain, f"MATCH (e:Entity {{uid: $uid}}) RETURN {_ENTITY_COLUMNS}", uid=uid)
    if not rows:
        return None
    facts = await db.cypher(
        domain,
        f"MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE a.uid = $uid OR b.uid = $uid RETURN {_FACT_COLUMNS} "
        "ORDER BY r.disbelief ASC",
        uid=uid,
    )
    episodes = await db.cypher(
        domain,
        "MATCH (ep:Episode)-[:MENTIONS]->(e:Entity {uid: $uid}) RETURN ep.uid AS uid, ep.source AS source, "
        "ep.title AS title, ep.content_status AS content_status, ep.created_at AS created_at "
        "ORDER BY created_at DESC LIMIT 50",
        uid=uid,
    )
    return {"entity": rows[0], "facts": [_fact(f) for f in facts], "episodes": episodes}


async def get_episodes(db: ArcadeDB, domain: str, uids: list[str]) -> list[dict]:
    if not uids:
        return []
    return await db.cypher(
        domain,
        "MATCH (ep:Episode) WHERE ep.uid IN $uids RETURN ep.uid AS uid, ep.source AS source, ep.title AS title, "
        "ep.content_status AS content_status, ep.created_at AS created_at",
        uids=uids,
    )


async def get_entities(db: ArcadeDB, domain: str, uids: list[str]) -> list[dict]:
    if not uids:
        return []
    return await db.cypher(domain, f"MATCH (e:Entity) WHERE e.uid IN $uids RETURN {_ENTITY_COLUMNS}", uids=uids)


async def search_entities(db: ArcadeDB, domain: str, vector: list[float], k: int) -> list[dict]:
    hits = await entity_candidates(db, domain, vector, k)
    if not hits:
        return []
    sim = {h["uid"]: h["similarity"] for h in hits}
    rows = await get_entities(db, domain, list(sim))
    return sorted(({**r, "similarity": sim[r["uid"]]} for r in rows), key=lambda r: -r["similarity"])


async def search_facts(db: ArcadeDB, domain: str, vector: list[float], k: int) -> list[dict]:
    """Nearest valid facts by embedding, across every relation type."""
    sims: dict[str, float] = {}
    for rel in await _relation_types(db, domain):
        rows = await db.sql(
            domain, "SELECT uid, invalid_at, distance FROM (SELECT expand(vector.neighbors(:i, :v, :k)))",
            i=f"{rel}[embedding]", v=vector, k=k,
        )
        for r in rows:
            if r.get("invalid_at") is None:
                sims[r["uid"]] = round(1 - r["distance"], 4)
    top = sorted(sims, key=lambda u: -sims[u])[:k]
    facts = await get_facts(db, domain, top)
    return sorted(({**f, "similarity": sims[f["uid"]]} for f in facts), key=lambda f: -f["similarity"])


async def expand(db: ArcadeDB, domain: str, entity_uids: list[str], limit: int = 40) -> list[dict]:
    """Valid facts touching these entities, strongest first."""
    if not entity_uids:
        return []
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NULL AND (a.uid IN $u OR b.uid IN $u) "
        f"RETURN {_FACT_COLUMNS} ORDER BY r.disbelief ASC LIMIT $n",
        u=entity_uids, n=limit,
    )
    return [_fact(r) for r in rows]


async def domain_stats(db: ArcadeDB, domain: str) -> dict:
    stats = {"entity_count": 0, "fact_count": 0, "invalid_fact_count": 0, "episode_count": 0,
             "pending_reviews": 0, "categories": {}}
    if not await db.exists(domain):
        return stats
    types = await db.cypher(domain, "MATCH (e:Entity) RETURN e.type AS type, count(e) AS n")
    facts = await db.cypher(domain, "MATCH (:Entity)-[r:Fact]->(:Entity) RETURN r.invalid_at IS NULL AS valid, count(r) AS n")
    episodes = await db.sql(domain, "SELECT count(*) AS n FROM Episode")
    reviews = await db.sql(domain, "SELECT count(*) AS n FROM ReviewItem WHERE status = 'pending'")
    stats["categories"] = {r["type"]: r["n"] for r in types}
    stats["entity_count"] = sum(stats["categories"].values())
    for r in facts:
        stats["fact_count" if r["valid"] else "invalid_fact_count"] = r["n"]
    stats["episode_count"] = episodes[0]["n"] if episodes else 0
    stats["pending_reviews"] = reviews[0]["n"] if reviews else 0
    return stats


# ── Head review queue ──────────────────────────────────────────────────────

async def add_review(db: ArcadeDB, domain: str, kind: str, summary: str, payload: dict) -> str:
    uid = new_uid("r")
    await db.sql(
        domain,
        "INSERT INTO ReviewItem SET uid = :uid, kind = :kind, status = 'pending', summary = :summary, "
        "payload = :payload, created_at = :now",
        uid=uid, kind=kind, summary=summary, payload=json.dumps(payload, default=str), now=now(),
    )
    return uid


async def list_reviews(db: ArcadeDB, domain: str, status: str = "pending", limit: int = 100) -> list[dict]:
    if not await db.exists(domain):
        return []
    rows = await db.sql(
        domain,
        "SELECT uid, kind, status, summary, payload, created_at, resolved_at, resolution FROM ReviewItem "
        "WHERE status = :s ORDER BY created_at DESC LIMIT :n",
        s=status, n=limit,
    )
    return [{**r, "payload": json.loads(r["payload"])} for r in rows]


async def get_review(db: ArcadeDB, domain: str, uid: str) -> dict | None:
    rows = await db.sql(domain, "SELECT uid, kind, status, summary, payload FROM ReviewItem WHERE uid = :uid", uid=uid)
    return {**rows[0], "payload": json.loads(rows[0]["payload"])} if rows else None


async def close_review(db: ArcadeDB, domain: str, uid: str, status: str, resolution: str) -> None:
    await db.sql(
        domain,
        "UPDATE ReviewItem SET status = :s, resolution = :res, resolved_at = :now WHERE uid = :uid",
        s=status, res=resolution, now=now(), uid=uid,
    )
