"""ArcadeDB connection and the neuron knowledge graph (HTTP API + Cypher/SQL).

One ArcadeDB database per domain:

    (:Entity)    neuron   uid, name, type, summary, aliases, keys, embedding, mentions, created_at, updated_at
    -[:<rel>]->  synapse  one edge type per ontology relation, all EXTENDS Fact:
                          uid, fact, sources, evidence, disbelief, embedding, created_at, updated_at,
                          invalid_at, invalid_reason
    (:Episode)   memory   uid, source, title, content, content_status, content_hash, status, error, created_at
    (:Episode)-[:MENTIONS]->(:Entity)
    ReviewItem (document) uid, kind, status, summary, payload (JSON), created_at, resolved_at, resolution
    Report     (document) uid, kind, period_start, period_end, created_at, headline, briefing, spoken,
                          stats (JSON), digest (JSON)
    ChatTurn   (document) one side of a person's conversation with the Head (core/chat.py)
    Setting    (document) a domain setting by key, e.g. "learning" (core/learning.py)

A synapse's strength is a noisy-OR over its independent sources:
    weight = 1 - disbelief,  disbelief = Π (1 - reliability of each distinct source)
so every new source that confirms a fact makes it stronger, and one weak source never makes it strong.
Superseded facts are not deleted: `invalid_at` is set, and they stay visible as history.
"""

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from core.names import name_key
from core.ontology import normalize_type
from core.sources import tier_of

load_dotenv()

DB_URL = os.getenv("ARCADEDB_URL", "http://localhost:2480")
DB_USER = os.getenv("ARCADEDB_USER", "root")
DB_PASS = os.getenv("ARCADEDB_PASS", "sovereign_pass")

ENTITY_INDEX = "Entity[embedding]"
STATE = "has_state"  # built-in relation for facts about a single entity (self-loop)
STATE_SIMILARITY = 0.55  # an entity's states this similar to a new one are compared with it (fact_candidates)
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
CREATE PROPERTY Episode.status IF NOT EXISTS STRING;
CREATE EDGE TYPE Fact IF NOT EXISTS;
CREATE EDGE TYPE MENTIONS IF NOT EXISTS;
CREATE DOCUMENT TYPE ReviewItem IF NOT EXISTS;
CREATE PROPERTY ReviewItem.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON ReviewItem (uid) UNIQUE;
CREATE DOCUMENT TYPE Report IF NOT EXISTS;
CREATE PROPERTY Report.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Report (uid) UNIQUE;
CREATE DOCUMENT TYPE Thread IF NOT EXISTS;
CREATE PROPERTY Thread.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Thread (uid) UNIQUE;
CREATE DOCUMENT TYPE Message IF NOT EXISTS;
CREATE PROPERTY Message.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Message (uid) UNIQUE;
CREATE PROPERTY Message.thread IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Message (thread) NOTUNIQUE;
CREATE PROPERTY Message.status IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Message (status) NOTUNIQUE;
CREATE DOCUMENT TYPE Setting IF NOT EXISTS;
CREATE PROPERTY Setting.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Setting (uid) UNIQUE;
CREATE DOCUMENT TYPE ChatTurn IF NOT EXISTS;
CREATE PROPERTY ChatTurn.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON ChatTurn (uid) UNIQUE;
CREATE PROPERTY ChatTurn.user IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON ChatTurn (user) NOTUNIQUE;
CREATE DOCUMENT TYPE ExamRun IF NOT EXISTS;
CREATE PROPERTY ExamRun.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON ExamRun (uid) UNIQUE;
"""
# Relation names that would collide with the schema's own types: every type above (2026-10-14: the list had been
# written by hand and missed Thread, Message, Setting, ChatTurn — a relation "message" would have been a document).
_RESERVED = {t.lower() for t in re.findall(r"CREATE (?:VERTEX|EDGE|DOCUMENT) TYPE (\w+)", _SCHEMA)}

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


def _tiered(rows: list[dict]) -> list[dict]:
    """Episode rows with their tier — read from the URL for those stored before tiers (2026-10-28)."""
    for r in rows:
        r["tier"] = r.get("tier") or tier_of(r.get("source") or "")
    return rows


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


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

async def ensure_domain_db(db: ArcadeDB, domain: str, create: bool = True) -> None:
    """Create the domain's database + schema if missing. Idempotent. With create=False a missing database is an
    error: a write that outlives its domain (a nightly step, a background task) must not bring the database back."""
    if domain in db._ready:
        return
    if not await db.exists(domain):
        if not create:
            raise ArcadeDBError(f"domain database '{domain}' does not exist")
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
    """The episode of this text: uid, source, status (None: stored before episodes had one — done), created_at."""
    rows = await db.sql(domain, "SELECT uid, source, status, created_at FROM Episode WHERE content_hash = :h",
                        h=content_hash)
    return {**rows[0], "status": rows[0].get("status")} if rows else None


async def create_episode(db: ArcadeDB, domain: str, episode: dict, status: str = "pending") -> None:
    """A new source. `status` says how far its ingest got (pending → done | failed), so a text whose ingest died
    half-way is read again instead of being skipped as a duplicate."""
    await db.cypher(domain, "CREATE (e:Episode) SET e += $props", props={**episode, "status": status})


async def set_episode_status(db: ArcadeDB, domain: str, uid: str, status: str, error: str | None = None) -> None:
    await db.cypher(domain, "MATCH (ep:Episode {uid: $uid}) SET ep.status = $s, ep.error = $e",
                    uid=uid, s=status, e=error)


async def set_episode_gaps(db: ArcadeDB, domain: str, episode_uid: str, gaps: dict[str, int]) -> None:
    """What the ontology could not hold in this source ("relation:uses": 3) — evidence for changing it."""
    await db.cypher(domain, "MATCH (ep:Episode {uid: $uid}) SET ep.ontology_gaps = $gaps",
                    uid=episode_uid, gaps=json.dumps(gaps))


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
    return _tiered(await db.cypher(
        domain,
        "MATCH (ep:Episode) OPTIONAL MATCH (ep)-[:MENTIONS]->(e:Entity) "
        "RETURN ep.uid AS uid, ep.source AS source, ep.title AS title, ep.content_status AS content_status, ep.tier AS tier, "
        "ep.status AS status, ep.created_at AS created_at, left(ep.content, 240) AS snippet, count(e) AS entities "
        "ORDER BY created_at DESC LIMIT $n",
        n=limit,
    ))


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
) -> str | None:
    """A new fact. Its uid — or None when no edge was written (an endpoint entity is gone: merged or pruned)."""
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
    rows = await db.cypher(
        domain,
        f"MATCH (a:Entity {{uid: $a}}), (b:Entity {{uid: $b}}) CREATE (a)-[r:`{rel}`]->(b) SET r += $props "
        "RETURN r.uid AS uid",
        a=source_uid, b=target_uid, props=props,
    )
    return uid if rows else None


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
    """Supersede a valid fact. One already superseded keeps its first date and reason — that is its history."""
    await db.cypher(
        domain,
        f"MATCH ()-[r:`{edge_type(relation)}`]->() WHERE r.uid = $uid AND r.invalid_at IS NULL "
        "SET r.invalid_at = $now, r.invalid_reason = $why",
        uid=fact_uid, now=now(), why=reason,
    )


async def fact_candidates(db: ArcadeDB, domain: str, source_uid: str, relation: str, target_uid: str,
                          embedding: list[float] | None = None) -> list[dict]:
    """Existing valid facts a new fact could restate or contradict: any fact between the same two
    entities, or the same subject with the same relation.
    A state (a fact about one entity) is the exception: every statement about the entity would qualify, and the
    Linker read a new one as the newer value of all of them — one superseded eight unrelated facts about the same
    country (2026-10-28; 429 of the 594 supersessions in neuron_test were such). Its candidates are the entity's
    states near it in meaning (`embedding`), the way a status and its change are ("…is sold out" / "…has stock
    again" ~0.66; the wrongly superseded mostly below 0.55); none without an embedding."""
    if source_uid == target_uid:
        if not embedding:
            return []
        rows = await db.cypher(domain, "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NULL "
                                       f"AND a.uid = $s AND b.uid = $s RETURN {_FACT_COLUMNS}, r.embedding AS embedding",
                               s=source_uid)
        near = sorted(((_cosine(embedding, r.pop("embedding")), r) for r in rows if r.get("embedding")),
                      key=lambda x: -x[0])
        return [{**_fact(r), "similarity": round(s, 4)} for s, r in near[:8] if s >= STATE_SIMILARITY]
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


async def repair_name_keys(db: ArcadeDB, domain: str) -> int:
    """A name key belongs to one entity — the one whose own name it is — or, when it is nobody's name (a shared
    descriptor such as "hyperscaler"), to none. Removes keys and aliases that leaked onto other entities.
    Returns how many entities were fixed. (Two entities with the same own name are left for a merge review.)"""
    rows = await db.sql(domain, "SELECT uid, name, keys, aliases FROM Entity")
    owners: dict[str, list[dict]] = {}
    for r in rows:
        for k in r.get("keys") or []:
            owners.setdefault(k, []).append(r)
    leaked: dict[str, set[str]] = {}
    for k, ents in owners.items():
        if len(ents) > 1:
            for r in ents:
                if name_key(r["name"]) != k:
                    leaked.setdefault(r["uid"], set()).add(k)
    for r in rows:
        if bad := leaked.get(r["uid"]):
            await db.cypher(
                domain, "MATCH (e:Entity {uid: $uid}) SET e.keys = $keys, e.aliases = $aliases", uid=r["uid"],
                keys=[k for k in r["keys"] if k not in bad],
                aliases=[a for a in r.get("aliases") or [] if name_key(a) not in bad],
            )
    return len(leaked)


async def prune_factless_entities(db: ArcadeDB, domain: str, keep: set[str] = frozenset(),
                                  dry_run: bool = False) -> list[dict]:
    """Delete the entities no fact touches any more (a weak fact rejected in review, a merge, older ingests that
    stored every name) — except those in `keep` (pending reviews). Returns what was (or would be)
    deleted, enough to restore it: the entity's fields and the episodes that mentioned it."""
    if not await db.exists(domain):
        return []
    rows = await db.cypher(
        domain,
        "MATCH (e:Entity) WHERE NOT (e)-[:Fact]-() OPTIONAL MATCH (ep:Episode)-[:MENTIONS]->(e) "
        "RETURN e.uid AS uid, e.name AS name, e.type AS type, e.summary AS summary, e.aliases AS aliases, "
        "e.keys AS keys, e.mentions AS mentions, e.created_at AS created_at, collect(ep.uid) AS episodes",
    )
    doomed = [r for r in rows if r["uid"] not in keep]
    if not dry_run:
        for i in range(0, len(doomed), 200):
            await db.cypher(domain, "MATCH (e:Entity) WHERE e.uid IN $uids DETACH DELETE e",
                            uids=[r["uid"] for r in doomed[i:i + 200]])
    return doomed


_ENTITY_REFS = ("entity", "candidate", "source", "target")   # review payload fields naming an entity: <x>_uid, <x>_name


async def merge_entities(db: ArcadeDB, domain: str, duplicate_uid: str, into_uid: str) -> None:
    """Fold a duplicate entity into another: names, mentions and every fact move over — each fact keeps its uid,
    embedding and every property, so links and reviews that cite it still find it. Pending reviews that
    name the duplicate are pointed at the survivor. ValueError when the survivor is gone (the review stays)."""
    if duplicate_uid == into_uid:
        raise ValueError(f"Cannot merge {into_uid} into itself")
    into = await get_entities(db, domain, [into_uid])
    if not into:
        raise ValueError(f"Entity {into_uid} no longer exists")
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
    # 2026-10-14: facts were re-created with a new uid and without their embedding — a merge cut them out of vector
    # search and broke every reference to them. One statement per fact: the copy and the delete commit together,
    # so the uid's unique index never sees two.
    for f in {f["uid"]: f for f in dup["facts"]}.values():
        await db.cypher(
            domain,
            f"MATCH (:Entity)-[old:`{edge_type(f['relation'])}`]->(:Entity) WHERE old.uid = $uid "
            f"MATCH (a:Entity {{uid: $a}}), (b:Entity {{uid: $b}}) "
            f"CREATE (a)-[r:`{edge_type(f['relation'])}`]->(b) SET r = properties(old) DELETE old",
            uid=f["uid"], a=into_uid if f["source_uid"] == duplicate_uid else f["source_uid"],
            b=into_uid if f["target_uid"] == duplicate_uid else f["target_uid"],
        )
    await db.cypher(domain, "MATCH (d:Entity {uid: $d}) DETACH DELETE d", d=duplicate_uid)
    await _rehome_reviews(db, domain, duplicate_uid, into[0])



async def _rehome_reviews(db: ArcadeDB, domain: str, duplicate_uid: str, into: dict) -> None:
    """Pending reviews that name a merged-away entity now name the survivor, so approving one later acts on it.
    A merge review left asking to merge the survivor into itself is closed: that is done. The review being applied
    (and any twin of it) is left as it is: its decider closes it."""
    for r in await list_reviews(db, domain, limit=100000):
        p = r["payload"]
        hit = [x for x in _ENTITY_REFS if p.get(f"{x}_uid") == duplicate_uid]
        if not hit or (r["kind"] == "merge" and (p.get("entity_uid"), p.get("candidate_uid")) == (duplicate_uid,
                                                                                                  into["uid"])):
            continue
        for x in hit:
            p[f"{x}_uid"] = into["uid"]
            if f"{x}_name" in p:
                p[f"{x}_name"] = into["name"]
        if r["kind"] == "merge" and p.get("entity_uid") == p.get("candidate_uid"):
            await close_review(db, domain, r["uid"], "approved", f"already one entity: merged into {into['name']}")
            continue
        await db.sql(domain, "UPDATE ReviewItem SET payload = :p WHERE uid = :uid AND status = 'pending'",
                     p=json.dumps(p, default=str), uid=r["uid"])


# ── Reads ──────────────────────────────────────────────────────────────────

async def graph(db: ArcadeDB, domain: str, limit: int = 0, include: list[str] | tuple = ()) -> dict:
    """Entities and the facts between them (superseded ones included, marked valid=false). With `limit`: the
    `limit` most connected entities (valid facts, then mentions) plus `include` — what a view can draw without lag;
    the graph itself keeps everything. `total` counts the whole graph."""
    if not await db.exists(domain):
        return {"entities": [], "facts": [], "total": {"entities": 0, "facts": 0}}
    total = {"entities": (await db.sql(domain, "SELECT count(*) AS n FROM Entity"))[0]["n"],
             "facts": (await db.cypher(domain, "MATCH (:Entity)-[r:Fact]->(:Entity) RETURN count(r) AS n"))[0]["n"]}
    if limit and limit < total["entities"]:
        ranked = await db.cypher(
            domain, "MATCH (e:Entity) OPTIONAL MATCH (e)-[r:Fact]-(:Entity) WHERE r.invalid_at IS NULL "
                    "WITH e, count(r) AS degree RETURN e.uid AS uid ORDER BY degree DESC, e.mentions DESC LIMIT $n",
            n=limit)
        uids = list(dict.fromkeys([r["uid"] for r in ranked] + list(include)))
        entities = await db.cypher(domain, f"MATCH (e:Entity) WHERE e.uid IN $uids RETURN {_ENTITY_COLUMNS}", uids=uids)
        facts = await db.cypher(domain, f"MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE a.uid IN $uids AND b.uid IN $uids "
                                        f"RETURN {_FACT_COLUMNS}", uids=uids)
    else:
        entities = await db.cypher(domain, f"MATCH (e:Entity) RETURN {_ENTITY_COLUMNS}")
        facts = await db.cypher(domain, f"MATCH (a:Entity)-[r:Fact]->(b:Entity) RETURN {_FACT_COLUMNS}")
    return {"entities": entities, "facts": [_fact(f) for f in facts], "total": total}


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
        "ep.title AS title, ep.content_status AS content_status, ep.tier AS tier, ep.created_at AS created_at "
        "ORDER BY created_at DESC LIMIT 50",
        uid=uid,
    )
    return {"entity": rows[0], "facts": [_fact(f) for f in facts], "episodes": _tiered(episodes)}


async def get_episodes(db: ArcadeDB, domain: str, uids: list[str]) -> list[dict]:
    if not uids:
        return []
    return _tiered(await db.cypher(
        domain,
        "MATCH (ep:Episode) WHERE ep.uid IN $uids RETURN ep.uid AS uid, ep.source AS source, ep.title AS title, "
        "ep.content_status AS content_status, ep.tier AS tier, ep.created_at AS created_at",
        uids=uids,
    ))


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
    """Nearest valid facts by embedding, across every relation type. Each index is asked for more than k: the
    superseded paraphrases of a fact sit right next to it and would otherwise take the valid hits' places."""
    sims: dict[str, float] = {}
    for rel in await _relation_types(db, domain):
        rows = await db.sql(
            domain, "SELECT uid, invalid_at, distance FROM (SELECT expand(vector.neighbors(:i, :v, :k)))",
            i=f"{rel}[embedding]", v=vector, k=max(k, min(k * 3, 30)),
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


async def list_reviews(db: ArcadeDB, domain: str, status: str = "pending", limit: int = 100, offset: int = 0,
                       oldest_first: bool = False) -> list[dict]:
    if not await db.exists(domain):
        return []
    rows = await db.sql(
        domain,
        "SELECT uid, kind, status, summary, payload, created_at, resolved_at, resolution FROM ReviewItem "
        f"WHERE status = :s ORDER BY created_at {'ASC' if oldest_first else 'DESC'} SKIP :o LIMIT :n",
        s=status, o=max(0, offset), n=limit,
    )
    return [{**r, "payload": json.loads(r["payload"])} for r in rows]


async def count_reviews(db: ArcadeDB, domain: str, status: str = "pending") -> int:
    if not await db.exists(domain):
        return 0
    rows = await db.sql(domain, "SELECT count(*) AS n FROM ReviewItem WHERE status = :s", s=status)
    return rows[0]["n"] if rows else 0


async def get_review(db: ArcadeDB, domain: str, uid: str) -> dict | None:
    rows = await db.sql(domain, "SELECT uid, kind, status, summary, payload FROM ReviewItem WHERE uid = :uid", uid=uid)
    return {**rows[0], "payload": json.loads(rows[0]["payload"])} if rows else None


async def close_review(db: ArcadeDB, domain: str, uid: str, status: str, resolution: str) -> bool:
    """Decide a pending item. True only if this call closed it (two deciders at once: one wins)."""
    rows = await db.sql(
        domain,
        "UPDATE ReviewItem SET status = :s, resolution = :res, resolved_at = :now WHERE uid = :uid "
        "AND status = 'pending'",
        s=status, res=resolution, now=now(), uid=uid,
    )
    return bool(rows and rows[0].get("count"))


async def referenced(db: ArcadeDB, domain: str) -> set[str]:
    """Every uid something still relies on: any value, however nested, of a pending review's payload (entity and
    fact uids, `supersedes`, `existing[].uid` …). Pruning and forgetting spare these."""
    keep: set[str] = set()

    def walk(v) -> None:
        if isinstance(v, str):
            keep.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    for r in await list_reviews(db, domain, limit=100000):
        walk(r.get("payload") or {})
    return keep


# ── Changes over a period (daily report, summary refresh) ──────────────────
# Timestamps are stored as UTC ISO-8601 strings. ArcadeDB's Cypher reads them as datetimes, so a comparison
# with a plain string parameter is always false: compare with datetime($since). SQL compares them as strings.

async def changes_since(db: ArcadeDB, domain: str, since: str, limit: int = 300) -> dict:
    """What the graph gained and lost since `since`: sources, entities, new / strengthened / superseded
    facts, and review items opened or decided."""
    empty = {"episodes": [], "entities": [], "facts_created": [], "facts_strengthened": [],
             "facts_invalidated": [], "reviews_opened": [], "reviews_decided": []}
    if not await db.exists(domain):
        return empty
    await ensure_domain_db(db, domain, create=False)
    episodes = await db.cypher(
        domain,
        "MATCH (ep:Episode) WHERE ep.created_at >= datetime($since) OPTIONAL MATCH (ep)-[:MENTIONS]->(e:Entity) "
        "RETURN ep.uid AS uid, ep.source AS source, ep.title AS title, ep.content_status AS content_status, ep.tier AS tier, "
        "ep.created_at AS created_at, ep.ontology_gaps AS ontology_gaps, count(e) AS entities ORDER BY created_at",
        since=since,
    )
    for ep in _tiered(episodes):
        ep["ontology_gaps"] = json.loads(ep.get("ontology_gaps") or "{}")
    entities = await db.cypher(
        domain,
        f"MATCH (e:Entity) WHERE e.created_at >= datetime($since) RETURN {_ENTITY_COLUMNS} "
        "ORDER BY e.mentions DESC LIMIT $n",
        since=since, n=limit,
    )

    async def facts(where: str, order: str) -> list[dict]:
        rows = await db.cypher(
            domain,
            f"MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE {where} RETURN {_FACT_COLUMNS} ORDER BY {order} LIMIT $n",
            since=since, n=limit,
        )
        return [_fact(r) for r in rows]

    reviews = await db.sql(
        domain,
        "SELECT uid, kind, status, summary, created_at, resolved_at, resolution FROM ReviewItem "
        "WHERE created_at >= :since OR resolved_at >= :since ORDER BY created_at",
        since=since,
    )
    return {
        "episodes": episodes,
        "entities": entities,
        "facts_created": await facts("r.created_at >= datetime($since) AND r.invalid_at IS NULL", "r.disbelief ASC"),
        # Updated after creation and still valid: another source confirmed it.
        "facts_strengthened": await facts(
            "r.updated_at >= datetime($since) AND r.created_at < datetime($since) AND r.invalid_at IS NULL",
            "r.disbelief ASC"),
        "facts_invalidated": await facts("r.invalid_at >= datetime($since)", "r.invalid_at DESC"),
        "reviews_opened": [r for r in reviews if r["created_at"] >= since],
        "reviews_decided": [r for r in reviews if (r.get("resolved_at") or "") >= since],
    }


async def changed_entities(db: ArcadeDB, domain: str, since: str, limit: int) -> list[str]:
    """Entities whose facts changed since `since` (created, strengthened or superseded), most changes first."""
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) "
        "WHERE r.created_at >= datetime($since) OR r.updated_at >= datetime($since) "
        "OR r.invalid_at >= datetime($since) "
        "RETURN a.uid AS a, b.uid AS b",
        since=since,
    )
    counts: dict[str, int] = {}
    for r in rows:
        for uid in {r["a"], r["b"]}:
            counts[uid] = counts.get(uid, 0) + 1
    return sorted(counts, key=lambda u: -counts[u])[:limit]


async def top_entities(db: ArcadeDB, domain: str, limit: int) -> list[dict]:
    """The most mentioned entities."""
    return await db.cypher(domain, f"MATCH (e:Entity) RETURN {_ENTITY_COLUMNS} ORDER BY e.mentions DESC LIMIT $n",
                           n=limit)


async def top_facts(db: ArcadeDB, domain: str, limit: int, valid: bool = True) -> list[dict]:
    """Strongest valid facts — or, with valid=False, the most recently superseded ones."""
    where, order = ("r.invalid_at IS NULL", "r.disbelief ASC, r.evidence DESC") if valid else \
        ("r.invalid_at IS NOT NULL", "r.invalid_at DESC")
    rows = await db.cypher(
        domain, f"MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE {where} RETURN {_FACT_COLUMNS} ORDER BY {order} LIMIT $n",
        n=limit,
    )
    return [_fact(r) for r in rows]


async def facts_changed_on(db: ArcadeDB, domain: str, entity_uids: list[str], since: str) -> int:
    """How many facts touching these entities were added, confirmed or superseded since `since`."""
    if not entity_uids:
        return 0
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE (a.uid IN $w OR b.uid IN $w) AND (r.created_at >= datetime($s) "
        "OR r.updated_at >= datetime($s) OR r.invalid_at >= datetime($s)) RETURN count(r) AS n",
        w=entity_uids, s=since,
    )
    return rows[0]["n"] if rows else 0


async def set_entity_summary(db: ArcadeDB, domain: str, uid: str, summary: str) -> None:
    """A rewritten summary. The embedding stays the one from the entity's first description: entity
    resolution's similarity threshold is calibrated on description-vs-description."""
    await db.cypher(domain, "MATCH (e:Entity {uid: $uid}) SET e.summary = $s, e.summary_at = $now",
                    uid=uid, s=summary, now=now())


# ── Reports ────────────────────────────────────────────────────────────────

async def save_report(db: ArcadeDB, domain: str, report: dict) -> str:
    await ensure_domain_db(db, domain, create=False)
    uid = new_uid("rep")
    await db.sql(
        domain,
        "INSERT INTO Report SET uid = :uid, kind = :kind, period_start = :ps, period_end = :pe, created_at = :now, "
        "headline = :headline, briefing = :briefing, spoken = :spoken, stats = :stats, digest = :digest",
        uid=uid, kind=report["kind"], ps=report["period_start"], pe=report["period_end"], now=now(),
        headline=report["headline"], briefing=report["briefing"], spoken=report["spoken"],
        stats=json.dumps(report["stats"], default=str), digest=json.dumps(report["digest"], default=str),
    )
    return uid


def _report(row: dict, full: bool) -> dict:
    out = {**row, "stats": json.loads(row["stats"])}
    if full:
        out["digest"] = json.loads(row["digest"])
    return out


async def list_reports(db: ArcadeDB, domain: str, limit: int = 30) -> list[dict]:
    if not await db.exists(domain):
        return []
    await ensure_domain_db(db, domain, create=False)
    rows = await db.sql(
        domain,
        "SELECT uid, kind, period_start, period_end, created_at, headline, briefing, spoken, stats FROM Report "
        "ORDER BY created_at DESC LIMIT :n",
        n=limit,
    )
    return [_report(r, full=False) for r in rows]


async def get_report(db: ArcadeDB, domain: str, uid: str) -> dict | None:
    if not await db.exists(domain):
        return None
    await ensure_domain_db(db, domain, create=False)
    rows = await db.sql(
        domain,
        "SELECT uid, kind, period_start, period_end, created_at, headline, briefing, spoken, stats, digest "
        "FROM Report WHERE uid = :uid",
        uid=uid,
    )
    return _report(rows[0], full=True) if rows else None


# ── Documents ──────────────────────────────────────────────────────────────
# Type and column names come from code, never from input. Nested values (params, results, evidence) are
# stored as JSON strings, like ReviewItem.payload; `json_fields` names them for encoding and decoding.

def _encode(doc: dict, json_fields: tuple[str, ...]) -> dict:
    return {k: json.dumps(v, default=str) if k in json_fields else v for k, v in doc.items()}


def _decode(row: dict, json_fields: tuple[str, ...]) -> dict:
    row = {k: v for k, v in row.items() if not k.startswith("@")}
    for k in json_fields:
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


async def insert_doc(db: ArcadeDB, domain: str, type_: str, doc: dict, json_fields: tuple[str, ...] = ()) -> dict:
    await ensure_domain_db(db, domain, create=False)   # a deleted domain's late write must not bring it back
    doc = {"uid": new_uid(type_.lower()[:4]), "created_at": now(), **doc}
    # Names quoted and parameters prefixed: some names are SQL keywords (trigger), as columns and as parameters.
    await db.sql(domain, f"INSERT INTO {type_} SET " + ", ".join(f"`{k}` = :p_{k}" for k in doc),
                 **{f"p_{k}": v for k, v in _encode(doc, json_fields).items()})
    return doc


async def find_docs(db: ArcadeDB, domain: str, type_: str, json_fields: tuple[str, ...] = (), where: str = "",
                    order: str = "created_at DESC", limit: int = 100, **params) -> list[dict]:
    if not await db.exists(domain):
        return []
    await ensure_domain_db(db, domain, create=False)
    rows = await db.sql(domain, f"SELECT FROM {type_}{' WHERE ' + where if where else ''} ORDER BY {order} LIMIT :n",
                        n=limit, **params)
    return [_decode(r, json_fields) for r in rows]


async def get_doc(db: ArcadeDB, domain: str, type_: str, uid: str, json_fields: tuple[str, ...] = ()) -> dict | None:
    rows = await find_docs(db, domain, type_, json_fields, "uid = :uid", limit=1, uid=uid)
    return rows[0] if rows else None


async def update_doc(db: ArcadeDB, domain: str, type_: str, uid: str, changes: dict,
                     json_fields: tuple[str, ...] = (), if_status: str | None = None) -> bool:
    """Apply changes; with `if_status`, only while the document still has that status (atomic: of two
    concurrent claims, only one changes it). True if a document changed."""
    sets = ", ".join(f"`{k}` = :set_{k}" for k in changes)  # quoted + prefixed, as in insert_doc
    cond = "uid = :uid" + (" AND status = :if_status" if if_status else "")
    params = {f"set_{k}": v for k, v in _encode(changes, json_fields).items()}
    rows = await db.sql(domain, f"UPDATE {type_} SET {sets} WHERE {cond}", uid=uid, if_status=if_status, **params)
    return bool(rows and rows[0].get("count"))


async def delete_doc(db: ArcadeDB, domain: str, type_: str, uid: str) -> None:
    await db.sql(domain, f"DELETE FROM {type_} WHERE uid = :uid", uid=uid)
