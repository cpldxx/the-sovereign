"""Neuron KG operations — the logic behind both the REST API and the MCP tools.

ingest   text → Episode → per chunk:
           Extractor (LLM)       entities + facts
           code gates            ontology types, entity actually in the text, fact endpoints exist
           Validator (LLM)       each fact supported by the text? + reliability
           Resolver              name keys (code) → embedding candidates → LLM only for the ambiguous
           Linker (LLM)          only for facts with existing candidates: restatement → strengthen,
                                 newer value → invalidate the old fact
         Ambiguous outcomes (weak facts, unsure merges, unsure links) go to the Head review queue.
query    question → entity + fact vector search → expand through the graph → facts with sources
"""

import hashlib
import re

from agents.extractor import STATE, extract
from agents.linker import link
from agents.resolver import resolve
from agents.validator import validate
from core import database as kgdb
from core.database import ArcadeDB, new_uid, now
from core.embeddings import embed
from core.names import name_key
from core.ontology import Ontology, normalize_type
from domains.registry import load_domain

COMMIT_RELIABILITY = 0.6   # supported facts below this go to Head review instead of the graph
CANDIDATE_SIMILARITY = 0.6  # entity embedding similarity that makes an existing entity a merge candidate
PARTIAL_CAP = 0.5           # reliability cap for facts from partially collected sources
CHUNK_CHARS = 8000          # long sources are processed in chunks of about this size
FACT_SIMILARITY = 0.75      # existing facts this similar to a new one are compared with it by the Linker


def _chunks(text: str) -> list[str]:
    """Split on paragraph boundaries into ~CHUNK_CHARS pieces."""
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        if current and len(current) + len(para) > CHUNK_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def _in_text(names: list[str], text: str) -> bool:
    lowered = " ".join(text.lower().split())
    return any(" ".join(n.lower().split()) in lowered for n in names if n.strip())


class Report:
    def __init__(self, episode_uid: str):
        self.episode_uid = episode_uid
        self.entities: list[dict] = []
        self.facts: list[dict] = []
        self.reviews: list[str] = []

    def entity(self, **kw) -> None:
        self.entities.append(kw)

    def fact(self, **kw) -> None:
        self.facts.append(kw)

    def as_dict(self) -> dict:
        def count(items, *statuses):
            return sum(1 for i in items if i["status"] in statuses)
        return {
            "episode_uid": self.episode_uid,
            "entities_created": count(self.entities, "created"),
            "entities_matched": count(self.entities, "matched"),
            "entities_dropped": count(self.entities, "dropped"),
            "facts_created": count(self.facts, "created"),
            "facts_strengthened": count(self.facts, "strengthened"),
            "facts_invalidated": sum(len(f.get("invalidated", [])) for f in self.facts),
            "facts_rejected": count(self.facts, "rejected"),
            "facts_review": count(self.facts, "review"),
            "review_items": len(self.reviews),
            "touched_uids": sorted({e["uid"] for e in self.entities if e.get("uid")}),
            "entities": self.entities,
            "facts": self.facts,
        }


async def ingest(
    db: ArcadeDB, domain: str, raw_text: str, source: str = "user_input",
    title: str | None = None, content_status: str = "full",
) -> dict:
    """Store a source as an Episode and grow the graph from it. Raises ValueError for unknown domains.

    content_status: "full" | "partial" (paywalled/truncated: facts get capped reliability).
    """
    dom = load_domain(domain)
    grammar = Ontology.model_validate(dom["ontology"])
    description = dom["config"].get("description") or domain
    await kgdb.ensure_domain_db(db, domain)

    text = raw_text.strip()
    digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
    if existing := await kgdb.find_episode(db, domain, digest):
        return {**Report(existing["uid"]).as_dict(), "duplicate": True}

    episode_uid = new_uid("ep")
    await kgdb.create_episode(db, domain, {
        "uid": episode_uid, "source": source, "title": title or "", "content": text,
        "content_status": content_status, "content_hash": digest, "created_at": now(),
    })
    report = Report(episode_uid)
    cap = PARTIAL_CAP if content_status == "partial" else 1.0
    for chunk in _chunks(text):
        await _ingest_chunk(db, domain, description, grammar, chunk, source, episode_uid, cap, report)
    return {**report.as_dict(), "duplicate": False}


async def _ingest_chunk(db, domain, description, grammar: Ontology, text, source, episode_uid, cap, report: Report):
    ex = await extract(text, domain, description, grammar, source)

    # ── Code gates: entities ───────────────────────────────────────────────
    kept: dict[str, dict] = {}   # name key → entity
    by_name: dict[str, str] = {}  # any name/alias key → canonical key
    for e in ex.entities:
        etype = normalize_type(e.type)
        key = name_key(e.name)
        names = [e.name, *e.aliases]
        if not key:
            continue
        if etype not in grammar.entity_types:
            report.entity(name=e.name, type=etype, status="dropped", reason="type not in the ontology")
            continue
        if not _in_text(names, text):
            report.entity(name=e.name, type=etype, status="dropped", reason="not mentioned in the source")
            continue
        if key in kept:  # same entity listed twice: keep one, union its aliases
            kept[key]["aliases"] = sorted(set(kept[key]["aliases"]) | set(e.aliases))
            continue
        kept[key] = {"name": e.name.strip(), "type": etype, "aliases": [a.strip() for a in e.aliases if a.strip()],
                     "description": e.description.strip()}
    for key, e in kept.items():
        for n in [e["name"], *e["aliases"]]:
            if k := name_key(n):
                by_name.setdefault(k, key)

    # ── Code gates: facts ──────────────────────────────────────────────────
    allowed = set(grammar.relation_types) | {STATE}
    facts = []
    for f in ex.facts:
        rel = normalize_type(f.relation)
        s, t = by_name.get(name_key(f.source)), by_name.get(name_key(f.target))
        if rel == STATE:
            t = s
        reason = (
            "relation not in the ontology" if rel not in allowed
            else "endpoint is not an extracted entity" if not (s and t)
            else "an entity related to itself" if s == t and rel != STATE
            else None
        )
        if reason:
            report.fact(fact=f.fact, relation=rel, source=f.source, target=f.target, status="rejected", reason=reason)
            continue
        facts.append({"fact": f.fact.strip(), "relation": rel, "source": s, "target": t})

    if not kept:
        return

    # ── Validate facts against the text ────────────────────────────────────
    verdicts = await validate(text, source, [f["fact"] for f in facts])
    commit, weak = [], []
    for f, v in zip(facts, verdicts):
        f["reliability"] = round(min(v.reliability, cap), 3)
        if not v.supported:
            report.fact(fact=f["fact"], relation=f["relation"], source=kept[f["source"]]["name"],
                        target=kept[f["target"]]["name"], status="rejected", reason=v.reason or "not supported by the source")
        elif f["reliability"] < COMMIT_RELIABILITY:
            weak.append(f)
        else:
            commit.append(f)

    # ── Embeddings (one call for entities and facts) ───────────────────────
    entity_keys = list(kept)
    texts = [f"{kept[k]['name']} ({kept[k]['type']}): {kept[k]['description']}" for k in entity_keys]
    texts += [f["fact"] for f in commit + weak]
    try:
        vectors = await embed(texts)
    except Exception as e:
        print(f"[KG] embedding failed, continuing without vectors: {type(e).__name__}: {e}", flush=True)
        vectors = [None] * len(texts)
    entity_vec = dict(zip(entity_keys, vectors[: len(entity_keys)]))
    for f, v in zip(commit + weak, vectors[len(entity_keys):]):
        f["embedding"] = v

    # ── Resolve entities: name keys → candidates → LLM for the ambiguous ───
    uid_of: dict[str, str] = {}
    ambiguous = []
    for key, e in kept.items():
        e["keys"] = sorted({key, *(name_key(a) for a in e["aliases"])} - {""})
        if match := await kgdb.entities_by_keys(db, domain, e["keys"]):
            uid_of[key] = match[0]["uid"]
            continue
        cands = []
        if entity_vec[key]:
            cands = [c for c in await kgdb.entity_candidates(db, domain, entity_vec[key], 5)
                     if c["similarity"] >= CANDIDATE_SIMILARITY]
        if cands:
            ambiguous.append((key, cands))
    resolutions = await resolve([
        {"name": kept[k]["name"], "type": kept[k]["type"], "description": kept[k]["description"], "candidates": c}
        for k, c in ambiguous
    ])
    unsure_merge = {}
    for (key, cands), r in zip(ambiguous, resolutions):
        if r.duplicate_of and r.confident:
            uid_of[key] = r.duplicate_of
        elif r.duplicate_of:
            unsure_merge[key] = next(c for c in cands if c["uid"] == r.duplicate_of)

    ts = now()
    for key, e in kept.items():
        if key in uid_of:
            # The name this text used becomes an alias of the existing entity (touch_entity skips its own name).
            await kgdb.touch_entity(db, domain, uid_of[key], e["keys"], [e["name"], *e["aliases"]], e["description"])
            report.entity(name=e["name"], type=e["type"], uid=uid_of[key], status="matched")
            continue
        uid_of[key] = new_uid("e")
        await kgdb.create_entity(db, domain, {
            "uid": uid_of[key], "name": e["name"], "type": e["type"], "summary": e["description"],
            "aliases": e["aliases"], "keys": e["keys"], "mentions": 1, "created_at": ts, "updated_at": ts,
        }, entity_vec[key])
        report.entity(name=e["name"], type=e["type"], uid=uid_of[key], status="created")
        if key in unsure_merge:
            cand = unsure_merge[key]
            report.reviews.append(await kgdb.add_review(
                db, domain, "merge", f"Is “{e['name']}” the same as “{cand['name']}”?",
                {"entity_uid": uid_of[key], "entity_name": e["name"], "candidate_uid": cand["uid"],
                 "candidate_name": cand["name"], "similarity": cand["similarity"], "episode_uid": episode_uid},
            ))
    await kgdb.link_mentions(db, domain, episode_uid, sorted(set(uid_of.values())))

    # ── Weak facts → Head review (not in the graph until approved) ────────
    for f in weak:
        s, t = kept[f["source"]], kept[f["target"]]
        report.reviews.append(await kgdb.add_review(
            db, domain, "fact", f"Low-reliability fact ({f['reliability']}): {f['fact']}",
            {"source_uid": uid_of[f["source"]], "source_name": s["name"], "relation": f["relation"],
             "target_uid": uid_of[f["target"]], "target_name": t["name"], "fact": f["fact"],
             "reliability": f["reliability"], "episode_uid": episode_uid, "source": source},
        ))
        report.fact(fact=f["fact"], relation=f["relation"], source=s["name"], target=t["name"], status="review",
                    reason=f"reliability {f['reliability']} < {COMMIT_RELIABILITY}")

    # ── Link facts to existing ones, then commit ───────────────────────────
    # Candidates: facts on the same entities (structural) + facts saying something similar (semantic) —
    # the latter catches an update stated about a different entity ("HBM3E is sold out" vs
    # "SK hynix says its HBM3E is no longer sold out").
    candidates = []
    for f in commit:
        f["source_uid"], f["target_uid"] = uid_of[f["source"]], uid_of[f["target"]]
        cands = await kgdb.fact_candidates(db, domain, f["source_uid"], f["relation"], f["target_uid"])
        if f["embedding"]:
            seen = {c["uid"] for c in cands}
            cands += [c for c in await kgdb.search_facts(db, domain, f["embedding"], 5)
                      if c["similarity"] >= FACT_SIMILARITY and c["uid"] not in seen]
        candidates.append(cands)
    to_link = [(i, c) for i, c in enumerate(candidates) if c]
    links = dict(zip(
        (i for i, _ in to_link),
        await link([{"fact": commit[i]["fact"], "candidates": c} for i, c in to_link]),
    ))
    for i, f in enumerate(commit):
        names = {"source": kept[f["source"]]["name"], "target": kept[f["target"]]["name"]}
        by_uid = {c["uid"]: c for c in candidates[i]}
        lk = links.get(i)
        if lk and lk.same_as and lk.confident:
            r = await kgdb.strengthen_fact(db, domain, by_uid[lk.same_as]["relation"], lk.same_as, episode_uid,
                                           f["reliability"])
            report.fact(fact=f["fact"], relation=f["relation"], **names, uid=lk.same_as, status="strengthened", **r)
            continue
        uid = await kgdb.create_fact(db, domain, f["source_uid"], f["relation"], f["target_uid"], f["fact"],
                                     f["embedding"], episode_uid, f["reliability"])
        invalidated = []
        if lk and lk.confident:
            for old in lk.supersedes:
                await kgdb.invalidate_fact(db, domain, by_uid[old]["relation"], old, f"superseded by {uid}: {f['fact']}")
                invalidated.append(old)
        elif lk and (lk.same_as or lk.supersedes):
            report.reviews.append(await kgdb.add_review(
                db, domain, "link", f"Does “{f['fact']}” restate or replace existing facts?",
                {"fact_uid": uid, "relation": f["relation"], "fact": f["fact"], "episode_uid": episode_uid,
                 "reliability": f["reliability"], "same_as": lk.same_as, "supersedes": lk.supersedes,
                 "existing": [{"uid": c["uid"], "relation": c["relation"], "fact": c["fact"]} for c in candidates[i]]},
            ))
        report.fact(fact=f["fact"], relation=f["relation"], **names, uid=uid, status="created",
                    weight=f["reliability"], invalidated=invalidated)


# ── Head review decisions ──────────────────────────────────────────────────

async def resolve_review(db: ArcadeDB, domain: str, review_uid: str, approve: bool, note: str = "") -> dict:
    """Apply (approve) or dismiss (reject) a pending review item. Raises ValueError if it can't."""
    item = await kgdb.get_review(db, domain, review_uid)
    if not item:
        raise ValueError(f"Review item {review_uid} not found")
    if item["status"] != "pending":
        raise ValueError(f"Review item {review_uid} is already {item['status']}")
    p = item["payload"]
    outcome = "dismissed"
    if approve and item["kind"] == "fact":
        [vector] = await embed([p["fact"]])
        uid = await kgdb.create_fact(db, domain, p["source_uid"], p["relation"], p["target_uid"], p["fact"],
                                     vector, p["episode_uid"], p["reliability"])
        outcome = f"fact {uid} added"
    elif approve and item["kind"] == "merge":
        await kgdb.merge_entities(db, domain, p["entity_uid"], p["candidate_uid"])
        outcome = f"{p['entity_name']} merged into {p['candidate_name']}"
    elif approve and item["kind"] == "link":
        existing = {c["uid"]: c for c in p["existing"]}
        if p.get("same_as") in existing:
            await kgdb.strengthen_fact(db, domain, existing[p["same_as"]]["relation"], p["same_as"],
                                       p["episode_uid"], p["reliability"])
            await kgdb.invalidate_fact(db, domain, p["relation"], p["fact_uid"], f"restates {p['same_as']}")
            outcome = f"merged into {p['same_as']}"
        superseded = [old for old in p.get("supersedes", []) if old in existing]
        for old in superseded:
            await kgdb.invalidate_fact(db, domain, existing[old]["relation"], old,
                                       f"superseded by {p['fact_uid']}: {p['fact']}")
        if superseded:
            outcome = f"superseded {len(superseded)} fact(s)"
    resolution = f"{outcome}{' — ' + note if note else ''}"
    await kgdb.close_review(db, domain, review_uid, "approved" if approve else "rejected", resolution)
    return {"uid": review_uid, "status": "approved" if approve else "rejected", "resolution": resolution}


# ── Query (Graph RAG) ──────────────────────────────────────────────────────

async def query(db: ArcadeDB, domain: str, question: str, k: int = 5) -> dict:
    """The entities and facts closest to the question, the facts around them (strongest first), and the
    sources behind those facts. Raises ValueError for unknown domains."""
    load_domain(domain)
    empty = {"domain": domain, "query": question, "entities": [], "facts": [], "episodes": []}
    if not await db.exists(domain):
        return empty
    [vector] = await embed([question])
    entities = await kgdb.search_entities(db, domain, vector, k)
    hit_facts = await kgdb.search_facts(db, domain, vector, k)
    seeds = {e["uid"] for e in entities} | {u for f in hit_facts for u in (f["source_uid"], f["target_uid"])}
    around = await kgdb.expand(db, domain, sorted(seeds), limit=40)

    facts, seen = [], set()
    for f in hit_facts + around:
        if f["uid"] not in seen:
            seen.add(f["uid"])
            facts.append(f)
    known = {e["uid"] for e in entities}
    endpoints = {u for f in facts for u in (f["source_uid"], f["target_uid"])} - known
    entities += await kgdb.get_entities(db, domain, sorted(endpoints))
    sources = sorted({s for f in facts[:20] for s in (f.get("sources") or [])})
    return {**empty, "entities": entities, "facts": facts,
            "episodes": await kgdb.get_episodes(db, domain, sources[:30])}
