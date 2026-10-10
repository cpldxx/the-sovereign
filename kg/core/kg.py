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
import time
from collections import Counter

from agents.extractor import STATE, extract, relate
from agents.linker import link
from agents.resolver import resolve
from agents.validator import validate
from core import database as kgdb
from core.database import ArcadeDB, new_uid, now
from core.embeddings import embed
from core.names import name_key
from core.ontology import Ontology, normalize_type
from core.tracing import Timings, observe
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


# Amounts, percentages, dates and reporting periods belong inside fact text, never as entities.
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_VALUE = re.compile(
    rf"^\s*(?:[$€£¥₩]|[\d.,\s]+$|.*\d\s*%|(?:Q[1-4]|H[12]|FY\s?\d{{2,4}})\b"
    rf"|(?:{_MONTHS})(?:\s+\d{{1,2}})?(?:,?\s+\d{{4}})?\s*$)",
    re.I,
)


def _stem(relation: str) -> tuple[str, bool]:
    """'manufactured_by' → ('manufactur', True): the verb stem, and whether it is the reverse (passive) form."""
    base = re.sub(r"_(by|to|with|for|in|on|from)$", "", relation)
    return re.sub(r"(ies|ied|es|ed|s|d|e)$", "", base), relation.endswith("_by")


def _conform(relation: str, allowed: set[str]) -> tuple[str, bool] | None:
    """An off-ontology relation that is a form of an allowed one → (that relation, whether source and target
    swap): manufactured_by → (manufactures, True), supplies_to → (supplies, False). None if there is none."""
    stem, passive = _stem(relation)
    if len(stem) < 4:
        return None
    for candidate in sorted(allowed):
        cstem, cpassive = _stem(candidate)
        if cstem == stem:
            return candidate, passive != cpassive
    return None


def _in_text(names: list[str], text: str) -> bool:
    lowered = " ".join(text.lower().split())
    return any(" ".join(n.lower().split()) in lowered for n in names if n.strip())


class Report:
    def __init__(self, episode_uid: str):
        self.episode_uid = episode_uid
        self.entities: list[dict] = []
        self.facts: list[dict] = []
        self.reviews: list[str] = []
        self.timings = Timings()  # seconds per pipeline stage
        self.chunks = 0
        self.gaps: Counter[str] = Counter()  # "relation:uses" / "type:person" → items the ontology could not hold

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
            "chunks": self.chunks,
            "ontology_gaps": dict(self.gaps.most_common()),
            "timings": dict(self.timings),
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
    start = time.perf_counter()
    with observe("ingest", input={"source": source, "title": title, "chars": len(text)},
                 metadata={"domain": domain, "episode_uid": episode_uid}, tags=[domain, "ingest"]) as obs:
        for chunk in _chunks(text):
            report.chunks += 1
            await _ingest_chunk(db, domain, description, grammar, chunk, source, episode_uid, cap, report)
        report.timings["total"] = round(time.perf_counter() - start, 2)
        if report.gaps:
            await kgdb.set_episode_gaps(db, domain, episode_uid, dict(report.gaps))
        result = {**report.as_dict(), "duplicate": False}
        if obs:
            obs.update(output={k: v for k, v in result.items() if isinstance(v, (int, dict)) and k != "entities"})
    return result


async def _restate(facts: list[dict], kept: dict, by_name: dict, relations: list[str]) -> list[dict]:
    """has_state facts that name another extracted entity → the relations they state, in their place (each then
    checked by the Validator like any fact). A statement no relation type fits stays a state."""
    asks = []
    for f in facts:
        if f["relation"] != STATE:
            continue
        others = [k for k, e in kept.items() if k != f["source"] and _in_text([e["name"], *e["aliases"]], f["fact"])]
        if others:
            asks.append((f, [f["source"], *others]))
    if not asks:
        return facts
    found = await relate([{"fact": f["fact"], "entities": [kept[k]["name"] for k in keys]} for f, keys in asks],
                         relations)
    restated, out, seen = set(), [], set()
    for r in found:
        if not 0 <= r.index < len(asks):
            continue
        f, keys = asks[r.index]
        rel, source, target = normalize_type(r.relation), r.source, r.target
        if rel not in relations and (conformed := _conform(rel, set(relations))):
            rel, swapped = conformed
            if swapped:
                source, target = target, source
        s, t = by_name.get(name_key(source)), by_name.get(name_key(target))
        if rel in relations and s in keys and t in keys and s != t and (s, rel, t, id(f)) not in seen:
            seen.add((s, rel, t, id(f)))
            out.append({"fact": f["fact"], "relation": rel, "source": s, "target": t,
                        "check": f"{kept[s]['name']} {rel.replace('_', ' ')} {kept[t]['name']}."})
            restated.add(id(f))
    return [f for f in facts if id(f) not in restated] + out


async def _ingest_chunk(db, domain, description, grammar: Ontology, text, source, episode_uid, cap, report: Report):
    stage = report.timings.stage
    with stage("extract"):
        ex = await extract(text, domain, description, grammar, source)

    # ── Code gates: entities ───────────────────────────────────────────────
    kept: dict[str, dict] = {}   # name key → entity
    by_name: dict[str, str] = {}  # any name/alias key → canonical key
    detail: set[str] = set()      # keys of dropped values / off-ontology things: facts about them are kept as states
    for e in ex.entities:
        etype = normalize_type(e.type)
        key = name_key(e.name)
        names = [e.name, *e.aliases]
        if not key:
            continue
        if _VALUE.match(e.name):
            report.entity(name=e.name, type=etype, status="dropped", reason="an amount, percentage or date")
            detail.add(key)
            continue
        if etype not in grammar.entity_types:
            report.entity(name=e.name, type=etype, status="dropped", reason="type not in the ontology")
            report.gaps[f"type:{etype}"] += 1
            detail.add(key)
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
        # An alias that is another extracted entity's name ("Samsung" listed with "SK Hynix") is a mistake.
        e["aliases"] = [a for a in e["aliases"] if name_key(a) == key or name_key(a) not in kept]
        for n in [e["name"], *e["aliases"]]:
            if k := name_key(n):
                by_name.setdefault(k, key)

    # ── Code gates: facts ──────────────────────────────────────────────────
    allowed = set(grammar.relation_types) | {STATE}
    facts = []
    for f in ex.facts:
        rel = normalize_type(f.relation)
        source, target = f.source, f.target
        if rel not in allowed and (conformed := _conform(rel, allowed - {STATE})):
            rel, swapped = conformed
            if swapped:
                source, target = target, source
        s, t = by_name.get(name_key(source)), by_name.get(name_key(target))
        if rel == STATE:
            t = s
        elif bool(s) != bool(t) and name_key(target if s else source) in detail:
            # "X reached $5.8 billion" / "X presented at <event>": the sentence is about X — keep it as X's state.
            rel, s, t = STATE, s or t, s or t
        reason = (
            "relation not in the ontology" if rel not in allowed
            else "endpoint is not an extracted entity" if not (s and t)
            else "an entity related to itself" if s == t and rel != STATE
            else None
        )
        if rel not in allowed:
            report.gaps[f"relation:{rel}"] += 1
        if reason:
            report.fact(fact=f.fact, relation=rel, source=f.source, target=f.target, status="rejected", reason=reason)
            continue
        facts.append({"fact": f.fact.strip(), "relation": rel, "source": s, "target": t})

    if not kept:
        return

    with stage("relate"):
        facts = await _restate(facts, kept, by_name, grammar.relation_types)

    # ── Validate facts against the text ────────────────────────────────────
    with stage("validate"):
        # A relation found in a statement is checked as that relation ("Samsung manufactures HBM3E"), not as the
        # statement it came from.
        verdicts = await validate(text, source, [f.get("check") or f["fact"] for f in facts])
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

    # ── Only entities something is known about ────────────────────────────
    # The extractor names everything a page mentions — menus, link text, report titles, list items. The graph holds
    # facts: an entity is written only as the endpoint of a fact that survived (stored, or waiting in review).
    # Measured before this gate (2026-10-10): 51% of ai_chips' entities had no fact at all.
    used = {f[end] for f in commit + weak for end in ("source", "target")}
    for key in [k for k in kept if k not in used]:
        e = kept.pop(key)
        report.entity(name=e["name"], type=e["type"], status="dropped", reason="no fact about it in the source")
    if not kept:
        return

    # ── Embeddings (one call for entities and facts) ───────────────────────
    entity_keys = list(kept)
    texts = [f"{kept[k]['name']} ({kept[k]['type']}): {kept[k]['description']}" for k in entity_keys]
    texts += [f["fact"] for f in commit + weak]
    with stage("embed"):
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
    with stage("resolve_lookup"):
        for key, e in kept.items():
            e["keys"] = sorted({key, *(name_key(a) for a in e["aliases"])} - {""})
            e["matches"] = await kgdb.entities_by_keys(db, domain, e["keys"])
            # Identity is decided by the entity's own name: the existing entity known by that name. A shared
            # alias alone ("hyperscaler"; "CoWoS-L" once listed under CoWoS) proves nothing — the Resolver decides.
            named = [m for m in e["matches"] if key in m["keys"]]
            if len(named) > 1:
                named = [m for m in named if name_key(m["name"]) == key] or named
            if len(named) == 1:
                uid_of[key] = named[0]["uid"]
                continue
            cands = [{**m, "summary": m.get("summary") or "", "similarity": None} for m in named or e["matches"]]
            if entity_vec[key]:
                seen = {c["uid"] for c in cands}
                cands += [c for c in await kgdb.entity_candidates(db, domain, entity_vec[key], 5)
                          if c["similarity"] >= CANDIDATE_SIMILARITY and c["uid"] not in seen]
            if cands:
                ambiguous.append((key, cands))
    with stage("resolve_llm"):
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

    with stage("write_entities"):
        ts = now()
        for key, e in kept.items():
            # Names another entity already owns stay with it: they never move onto this one.
            others = {k for m in e["matches"] if m["uid"] != uid_of.get(key) for k in m["keys"]}
            keys = [k for k in e["keys"] if k == key or k not in others]
            aliases = [a for a in e["aliases"] if name_key(a) not in others]
            if key in uid_of:
                # The name this text used becomes an alias of the existing entity (touch_entity skips its own name).
                await kgdb.touch_entity(db, domain, uid_of[key], keys, [e["name"], *aliases], e["description"])
                report.entity(name=e["name"], type=e["type"], uid=uid_of[key], status="matched")
                continue
            uid_of[key] = new_uid("e")
            await kgdb.create_entity(db, domain, {
                "uid": uid_of[key], "name": e["name"], "type": e["type"], "summary": e["description"],
                "aliases": aliases, "keys": keys, "mentions": 1, "created_at": ts, "updated_at": ts,
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
    with stage("link_lookup"):
        candidates = []
        for f in commit:
            f["source_uid"], f["target_uid"] = uid_of[f["source"]], uid_of[f["target"]]
            cands = await kgdb.fact_candidates(db, domain, f["source_uid"], f["relation"], f["target_uid"])
            if f["embedding"]:
                seen = {c["uid"] for c in cands}
                cands += [c for c in await kgdb.search_facts(db, domain, f["embedding"], 5)
                          if c["similarity"] >= FACT_SIMILARITY and c["uid"] not in seen]
            candidates.append(cands)
    with stage("link_llm"):
        to_link = [(i, c) for i, c in enumerate(candidates) if c]
        links = dict(zip(
            (i for i, _ in to_link),
            await link([{"fact": commit[i]["fact"], "candidates": c} for i, c in to_link]),
        ))
    with stage("write_facts"):
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

async def relink_states(db: ArcadeDB, domain: str, dry_run: bool = False, batch: int = 10,
                        unconnected_only: bool = True) -> dict:
    """Stored has_state facts that name another entity of the graph → the relations they state, added as facts:
    each checked by the Validator against its statement, carrying the statement's sources. The states stay; each one
    read is marked `relinked`, so it is asked once. (New sources get this at ingest: _restate.)
    unconnected_only: just the states of entities linked to no other entity — the ones this matters most for (all
    464 states of ai_chips took over 50 minutes on the local model; its ~70 unconnected ones, minutes)."""
    relations = Ontology.model_validate(load_domain(domain)["ontology"]).relation_types
    await kgdb.ensure_domain_db(db, domain)
    ents = await db.cypher(domain, "MATCH (e:Entity) RETURN e.uid AS uid, e.name AS name, e.aliases AS aliases")
    by_key: dict[str, str] = {}
    for e in ents:
        for n in [e["name"], *(e.get("aliases") or [])]:
            if k := name_key(n):
                by_key.setdefault(k, e["uid"])
    names = {e["uid"]: e["name"] for e in ents}
    alone = " AND NOT exists { MATCH (e)-[:Fact]-(o:Entity) WHERE o <> e }" if unconnected_only else ""
    states = await db.cypher(
        domain, "MATCH (e:Entity)-[r:has_state]->(e) WHERE r.invalid_at IS NULL AND r.relinked IS NULL" + alone +
                " RETURN r.uid AS uid, r.fact AS fact, e.uid AS subject, r.sources AS sources, r.disbelief AS disbelief")
    asks = []
    for st in states:
        others = [e["uid"] for e in ents if e["uid"] != st["subject"]
                  and _in_text([e["name"], *(e.get("aliases") or [])], st["fact"])]
        if others:
            asks.append((st, [st["subject"], *others]))
    added, proposed, skipped = [], 0, Counter()
    for i in range(0, len(asks), batch):
        part = asks[i:i + batch]
        found = await relate([{"fact": st["fact"], "entities": [names[u] for u in uids]} for st, uids in part],
                             relations)
        per: dict[int, list[tuple[str, str, str]]] = {}
        for r in found:
            if not 0 <= r.index < len(part):
                continue
            st, uids = part[r.index]
            rel, source, target = normalize_type(r.relation), r.source, r.target
            if rel not in relations and (conformed := _conform(rel, set(relations))):
                rel, swapped = conformed
                if swapped:
                    source, target = target, source
            s, tg = by_key.get(name_key(source)), by_key.get(name_key(target))
            if rel in relations and s in uids and tg in uids and s != tg and (s, rel, tg) not in per.get(r.index, []):
                per.setdefault(r.index, []).append((s, rel, tg))
        for j, triples in per.items():
            st, _ = part[j]
            proposed += len(triples)
            verdicts = await validate(st["fact"], "a stored statement",
                                      [f"{names[s]} {rel.replace('_', ' ')} {names[tg]}." for s, rel, tg in triples])
            for (s, rel, tg), v in zip(triples, verdicts):
                trust = round(min(v.reliability, 1.0 - (st.get("disbelief") or 0.0)), 3)
                if not v.supported or trust < COMMIT_RELIABILITY:
                    skipped["not supported" if not v.supported else "too weak"] += 1
                    continue
                if any(c["source_uid"] == s and c["target_uid"] == tg and c["relation"] == kgdb.edge_type(rel)
                       for c in await kgdb.fact_candidates(db, domain, s, rel, tg)):
                    skipped["already in the graph"] += 1
                    continue
                added.append({"source": names[s], "relation": rel, "target": names[tg], "fact": st["fact"],
                              "reliability": trust})
                if dry_run:
                    continue
                sources = st.get("sources") or []
                vec = (await embed([st["fact"]]) or [None])[0]
                uid = await kgdb.create_fact(db, domain, s, rel, tg, st["fact"], vec,
                                             sources[0] if sources else "", trust)
                await db.cypher(domain, "MATCH ()-[r:Fact {uid: $u}]->() SET r.sources = $src, r.evidence = $n",
                                u=uid, src=sources, n=max(len(sources), 1))
                for ep in sources:
                    await kgdb.link_mentions(db, domain, ep, [s, tg])
        if not dry_run:
            await db.cypher(domain, "MATCH ()-[r:has_state]->() WHERE r.uid IN $u SET r.relinked = true",
                            u=[st["uid"] for st, _ in part])
    return {"dry_run": dry_run, "statements": len(asks), "proposed": proposed, "added": len(added),
            "skipped": dict(skipped), "facts": added}


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
    with observe("query", input=question, metadata={"domain": domain}, tags=[domain, "query"]) as obs:
        result = await _query(db, domain, question, k, empty)
        if obs:
            obs.update(output={"entities": [e["name"] for e in result["entities"][:20]],
                               "facts": [f["fact"] for f in result["facts"][:20]]})
    return result


async def _query(db: ArcadeDB, domain: str, question: str, k: int, empty: dict) -> dict:
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
