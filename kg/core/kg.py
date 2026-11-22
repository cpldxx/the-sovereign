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

import asyncio
import hashlib
import os
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from pydantic_ai import Agent

from agents.extractor import STATE, extract, relate
from agents.linker import link, link_or_none
from agents.resolver import resolve
from agents.validator import judge
from core import activity, sources
from core import database as kgdb
from core.database import ArcadeDB, ArcadeDBError, new_uid, now
from core.embeddings import embed
from core.llm import model_for, model_settings
from core.names import name_key
from core.ontology import Ontology, normalize_type
from core.tracing import Timings, observe
from domains.registry import load_domain

COMMIT_RELIABILITY = 0.6   # supported facts below this go to Head review instead of the graph
CANDIDATE_SIMILARITY = 0.6  # entity embedding similarity that makes an existing entity a merge candidate
PARTIAL_CAP = 0.5           # reliability cap for facts from partially collected sources
CHUNK_CHARS = 8000          # long sources are processed in chunks of about this size
# Reading (model calls only) runs this many chunks at once, across every ingest in this process; writing is one chunk
# at a time per domain. Matches the model server's parallel slots (OLLAMA_NUM_PARALLEL).
INGEST_CONCURRENCY = int(os.getenv("INGEST_CONCURRENCY", "4"))
_reading = asyncio.Semaphore(INGEST_CONCURRENCY)
_writing: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
FACT_SIMILARITY = 0.75      # existing facts this similar to a new one are compared with it by the Linker
AROUND = 150                # facts around a query's entities ranked by closeness to it (strongest first, to this many)


# Pages come as markdown with every link's URL, image tags, menus, "related" lists and share buttons — tokens the
# model reads and junk it extracts. Measured on three research pages (2026-10-12): 38k → 27k characters, the
# newsletter page −65%, article text untouched. The episode keeps the original; only what the model reads is cleaned.
# One character or one (…) group per step: a nested "(x+)*" here froze the KG for 6 days on a link that never closed
# (2026-10-15 — each extra character doubled the time, and the regex runs on the event loop).
_LINK = re.compile(r"!?\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)")
_SHARE = re.compile(r"^(?:share (?:on|via|to) \w+[\s|·•,]*)+$", re.I)


def readable(text: str) -> str:
    """Markdown → the text worth reading: links become their text, images go, and so do lines that are only links
    (menus, related lists, footers) or share buttons."""
    out = []
    for line in text.splitlines():
        plain = _LINK.sub(lambda m: "" if m.group(0).startswith("!") else m.group(1), line)
        letters = len(re.sub(r"[\W_]+", "", plain))
        linked = sum(len(re.sub(r"[\W_]+", "", m.group(1))) for m in _LINK.finditer(line) if not m.group(0).startswith("!"))
        # A short line that is (almost) only links: a menu or footer item. A linked headline ("Samsung Q3 profit
        # jumps nine-fold…") is longer and states a fact — it stays (newsletters are made of them).
        if linked and linked >= 0.7 * letters and letters < 40:
            continue
        if _SHARE.match(plain.strip()):
            continue
        out.append(plain)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _pieces(text: str) -> list[str]:
    """Paragraphs — and a paragraph longer than a chunk split on its lines, then its sentences. Many pages have no
    blank lines at all: a 21k-character page used to go to the extractor as one piece (2026-10-12)."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        if len(para) <= CHUNK_CHARS:
            out.append(para)
            continue
        for line in para.split("\n"):
            out += [line] if len(line) <= CHUNK_CHARS else re.split(r"(?<=[.!?])\s+", line)
    return out


def _chunks(text: str) -> list[str]:
    """Split on paragraph (else line, else sentence) boundaries into ~CHUNK_CHARS pieces."""
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks, current = [], ""
    for para in _pieces(text):
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


@lru_cache(maxsize=4096)
def _mention(name: str) -> re.Pattern:
    """A name as a word: an end in Latin letters or digits must not run on into another word — "AI" was found in
    "said", "Arm" in "pharmaceutical" (2026-10-14). Scripts written without spaces (삼성전자는) match as they stand.
    A plural or possessive ending still counts (GPU → GPUs, Nvidia's)."""
    edge = re.compile(r"[a-z0-9]")
    head = r"(?<![a-z0-9])" if edge.match(name[0]) else ""
    tail = r"(?:'?s|es)?(?![a-z0-9])" if edge.match(name[-1]) else ""
    return re.compile(head + re.escape(name) + tail)


def _in_text(names: list[str], text: str) -> bool:
    lowered = " ".join(text.lower().split())
    return any(_mention(n).search(lowered) for n in (" ".join(n.lower().split()) for n in names) if n)


def _latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isascii() for c in letters) / len(letters) if letters else 1.0


def _translated(names: list[str], text: str) -> bool:
    """An English name for a thing a non-Latin text names in its own script (삼성전자 → Samsung Electronics) can't
    be found in it by spelling. Measured 2026-10-13: a Korean news page lost all 10 entities and 8 facts to the
    mention gate. Such entities pass it; their facts are still checked against the text by the Validator."""
    return _latin_share(text) < 0.5 and all(_latin_share(n) > 0.9 for n in names if n.strip())


class Report:
    def __init__(self, episode_uid: str):
        self.episode_uid = episode_uid
        self.entities: list[dict] = []
        self.facts: list[dict] = []
        self.reviews: list[str] = []
        self.timings = Timings()  # seconds per pipeline stage
        self.label = ""           # what is being read, for the agent view
        self.chunks = 0
        self.gaps: Counter[str] = Counter()  # "relation:uses" / "type:person" → items the ontology could not hold
        self.failure = ""         # why reading or writing this source failed (the episode is then "failed")

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
    title: str | None = None, content_status: str = "full", tier: str | None = None,
) -> dict:
    """Store a source as an Episode and grow the graph from it. Raises ValueError for unknown domains.

    content_status: "full" | "partial" (paywalled/truncated: facts get capped reliability).
    tier: what kind of source (core/sources.py) — read from the URL unless the caller knows (research: "paper"); it
    caps each fact's reliability too.
    The result says duplicate (this text was read already, or is being read: pending) or, when a model call or a
    write failed, failed + reason — the episode is then "failed" and the same text is read again next time.
    """
    dom = load_domain(domain)
    grammar = Ontology.model_validate(dom["ontology"])
    description = dom["config"].get("description") or domain
    await kgdb.ensure_domain_db(db, domain)

    text = raw_text.strip()
    digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
    existing = await kgdb.find_episode(db, domain, digest)
    if existing and not _retry(existing):
        return {**Report(existing["uid"]).as_dict(), "duplicate": True,
                **({"pending": True} if existing.get("status") == "pending" else {})}
    episode_uid = existing["uid"] if existing else new_uid("ep")
    tier = sources.tier_of(source, tier)
    _active.add(episode_uid)   # before any await: a second call with the same text sees it taken
    try:
        if existing:
            # A failed (or abandoned) read is read again under the same episode: what it wrote before is matched,
            # and strengthening counts an episode once.
            await kgdb.set_episode_status(db, domain, episode_uid, "pending")
        else:
            try:
                await kgdb.create_episode(db, domain, {
                    "uid": episode_uid, "source": source, "title": title or "", "content": text, "tier": tier,
                    "content_status": content_status, "content_hash": digest, "created_at": now(),
                }, status="pending")
            except ArcadeDBError:
                # The same text arriving twice at once: the content_hash index lets one in.
                if again := await kgdb.find_episode(db, domain, digest):
                    return {**Report(again["uid"]).as_dict(), "duplicate": True,
                            **({"pending": True} if again.get("status") == "pending" else {})}
                raise
        return await _ingest(db, domain, grammar, description, text, source, title, content_status, episode_uid, tier)
    finally:
        _active.discard(episode_uid)


# An episode is created before any model call (its content_hash is what makes a text a duplicate). Until
# 2026-10-14 one whose read failed blocked that text for good: re-ingesting it answered duplicate=True and research
# skipped the URL. Now it carries a status: pending → done | failed; failed or long-pending ones are read again.
PENDING_STALE = timedelta(hours=1)
_active: set[str] = set()   # episodes being read in this process
# One process reads (a single uvicorn worker): a pending episode from before it started lost its reader to the
# restart. 2026-11-11: pages read just before a crash were skipped as "being read" for an hour after it.
STARTED = datetime.now(timezone.utc).replace(microsecond=0)   # as precise as an episode's created_at


def _retry(episode: dict) -> bool:
    """Whether an existing episode with this text should be read again: it failed, or it is pending with no reader
    left (from before this process started, or so long ago). None = an episode from before statuses: done."""
    status = episode.get("status")
    if status == "failed":
        return episode["uid"] not in _active
    if status != "pending" or episode["uid"] in _active:
        return False
    try:
        started = datetime.fromisoformat(str(episode.get("created_at")))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started < STARTED or datetime.now(timezone.utc) - started > PENDING_STALE


async def _ingest(db, domain, grammar, description, text, source, title, content_status, episode_uid,
                  tier: str = "web") -> dict:
    report = Report(episode_uid)
    report.label = (title or source)[:100]
    # A fact is trusted no more than its source: a partial read, and the kind of source (core/sources.py).
    cap = min(PARTIAL_CAP if content_status == "partial" else 1.0, sources.cap(tier))
    start = time.perf_counter()
    with observe("ingest", input={"source": source, "title": title, "chars": len(text)},
                 metadata={"domain": domain, "episode_uid": episode_uid}, tags=[domain, "ingest"]) as obs:
        chunks = _chunks(readable(text))
        report.chunks = len(chunks)

        async def read(chunk):
            async with _reading:
                return await _read_chunk(domain, description, grammar, chunk, source, cap, report)
        try:
            reads = await asyncio.gather(*(read(c) for c in chunks))
            # A chunk the model couldn't read fails the whole source before anything is written: the next try
            # starts clean instead of re-reading chunks already written.
            if not report.failure:
                async with _writing[domain]:
                    for r in reads:
                        if r:
                            await _write_chunk(db, domain, r, source, episode_uid, report)
        except Exception as e:
            report.failure = f"{type(e).__name__}: {e}"[:500]
            print(f"[KG] ingest of {source} failed: {report.failure}", flush=True)
        report.timings["total"] = round(time.perf_counter() - start, 2)
        if report.gaps:
            await kgdb.set_episode_gaps(db, domain, episode_uid, dict(report.gaps))
        await kgdb.set_episode_status(db, domain, episode_uid, "failed" if report.failure else "done",
                                      error=report.failure or None)
        result = {**report.as_dict(), "duplicate": False, "failed": bool(report.failure)}
        if report.failure:
            result["reason"] = report.failure
        if obs:
            obs.update(output={k: v for k, v in result.items() if isinstance(v, (int, dict)) and k != "entities"})
    return result


# The agent each model stage is, for the live agent view.
_STAGE_AGENT = {"extract": "extractor", "relate": "linker", "validate": "validator", "resolve_llm": "resolver",
                "link_llm": "linker"}


@contextmanager
def _doing(report: "Report", domain: str, stage: str):
    """A pipeline stage: timed, and — for a model stage — its agent shown as working on this source."""
    with report.timings.stage(stage):
        if agent := _STAGE_AGENT.get(stage):
            with activity.working(domain, agent, report.label):
                yield
        else:
            yield


def _with_link(f: dict, kept: dict) -> str:
    """The fact as the Validator reads it: its sentence, and for a relation the edge it becomes."""
    if f["relation"] == STATE:
        return f["fact"]
    return f"{f['fact']} ⟨{kept[f['source']]['name']} —{f['relation']}→ {kept[f['target']]['name']}⟩"


async def _restate(facts: list[dict], kept: dict, by_name: dict, relations: list[str]) -> list[dict]:
    """has_state facts that name another extracted entity → the relations they state, in their place (each then
    checked by the Validator like any fact). A statement no relation type fits stays a state."""
    # Only for entities this chunk links to nothing else: the ones that would float alone. Asking about every state
    # that names another entity cost more model time than extraction itself (2026-10-12, 77 facts on 3 pages).
    linked = {f[end] for f in facts if f["relation"] != STATE for end in ("source", "target")}
    asks = []
    for f in facts:
        if f["relation"] != STATE or f["source"] in linked:
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


@dataclass
class _Read:
    """What reading a chunk produced — no database touched yet."""
    kept: dict
    commit: list
    weak: list
    entity_vec: dict


async def _read_chunk(domain, description, grammar: Ontology, text, source, cap, report: Report) -> _Read | None:
    """Extract → code gates → relate → validate → embed: model calls only, so chunks are read side by side."""
    with _doing(report, domain, "extract"):
        ex = await extract(text, domain, description, grammar, source)
    if ex is None:
        report.failure = report.failure or "the Extractor's model call failed"
        return None

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
        if not _in_text(names, text) and not _translated(names, text):
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
        return None

    with _doing(report, domain, "relate"):
        facts = await _restate(facts, kept, by_name, grammar.relation_types)

    # ── Validate facts against the text ────────────────────────────────────
    with _doing(report, domain, "validate"):
        # A fact is checked as the edge it becomes, not only as its sentence: "HBM is made by Samsung, SK hynix and
        # Micron" is true, yet ⟨Samsung —supplies→ Micron⟩ is not (seen 2026-10-12). A relation found in a statement
        # is checked as that relation ("Samsung manufactures HBM3E").
        verdicts = await judge(text, source, [f.get("check") or _with_link(f, kept) for f in facts])
    if verdicts is None:
        report.failure = report.failure or "the Validator's model call failed"
        return None
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
        return None

    # ── Embeddings (one call for entities and facts) ───────────────────────
    entity_keys = list(kept)
    texts = [f"{kept[k]['name']} ({kept[k]['type']}): {kept[k]['description']}" for k in entity_keys]
    texts += [f["fact"] for f in commit + weak]
    with _doing(report, domain, "embed"):
        try:
            vectors = await embed(texts)
        except Exception as e:
            print(f"[KG] embedding failed, continuing without vectors: {type(e).__name__}: {e}", flush=True)
            vectors = [None] * len(texts)
    entity_vec = dict(zip(entity_keys, vectors[: len(entity_keys)]))
    for f, v in zip(commit + weak, vectors[len(entity_keys):]):
        f["embedding"] = v
    return _Read(kept, commit, weak, entity_vec)


async def _write_chunk(db, domain, r: _Read, source, episode_uid, report: Report):
    """Resolve → write entities → reviews → link → write facts. Run one chunk at a time per domain (_writing): a
    chunk must see the entities and facts the previous one wrote, or two chunks naming the same new thing would
    both create it."""
    kept, commit, weak, entity_vec = r.kept, r.commit, r.weak, r.entity_vec

    # ── Resolve entities: name keys → candidates → LLM for the ambiguous ───
    uid_of: dict[str, str] = {}
    ambiguous = []
    with _doing(report, domain, "resolve_lookup"):
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
    with _doing(report, domain, "resolve_llm"):
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

    with _doing(report, domain, "write_entities"):
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
    with _doing(report, domain, "link_lookup"):
        candidates = []
        for f in commit:
            f["source_uid"], f["target_uid"] = uid_of[f["source"]], uid_of[f["target"]]
            cands = await kgdb.fact_candidates(db, domain, f["source_uid"], f["relation"], f["target_uid"],
                                               f["embedding"])
            if f["embedding"]:
                seen = {c["uid"] for c in cands}
                cands += [c for c in await kgdb.search_facts(db, domain, f["embedding"], 5)
                          if c["similarity"] >= FACT_SIMILARITY and c["uid"] not in seen]
            candidates.append(cands)
    with _doing(report, domain, "link_llm"):
        to_link = [(i, c) for i, c in enumerate(candidates) if c]
        links = dict(zip(
            (i for i, _ in to_link),
            await link([{"fact": commit[i]["fact"], "candidates": c} for i, c in to_link]),
        ))
    with _doing(report, domain, "write_facts"):
        for i, f in enumerate(commit):
            names = {"source": kept[f["source"]]["name"], "target": kept[f["target"]]["name"]}
            by_uid = {c["uid"]: c for c in candidates[i]}
            lk = links.get(i)
            if lk and lk.same_as and lk.confident:
                if await _counted(db, domain, by_uid[lk.same_as], source, episode_uid):
                    report.fact(fact=f["fact"], relation=f["relation"], **names, uid=lk.same_as, status="known",
                                reason="already counted from this source")
                    continue
                r = await kgdb.strengthen_fact(db, domain, by_uid[lk.same_as]["relation"], lk.same_as, episode_uid,
                                               f["reliability"])
                report.fact(fact=f["fact"], relation=f["relation"], **names, uid=lk.same_as, status="strengthened", **r)
                continue
            uid = await kgdb.create_fact(db, domain, f["source_uid"], f["relation"], f["target_uid"], f["fact"],
                                         f["embedding"], episode_uid, f["reliability"])
            if uid is None:
                # An endpoint was deleted (pruned, merged away) between reading and writing.
                report.fact(fact=f["fact"], relation=f["relation"], **names, status="rejected",
                            reason="endpoint vanished")
                continue
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
    if commit:
        activity.event(domain, "linker", "graph", "write", f"{len(commit)} facts from {report.label}")


async def known_sources(db: ArcadeDB, domain: str, urls: list[str]) -> list[str]:
    """The URLs among `urls` the graph has read: an episode from them is done (or older than episode statuses).
    A failed or unfinished read doesn't count — research fetches those pages again."""
    wanted = sorted({u for u in urls if u})
    if not wanted or not await db.exists(domain):
        return []
    rows = await db.cypher(domain, "MATCH (ep:Episode) WHERE ep.source IN $u RETURN ep.source AS source, "
                                   "ep.status AS status", u=wanted)
    done = {r["source"] for r in rows if r.get("status") in (None, "done")}
    return [u for u in dict.fromkeys(urls) if u in done]


async def _counted(db, domain, fact: dict, source: str, episode_uid: str) -> bool:
    """Whether this fact already counts a reading of this web page. A page read again (it changed a little, so it is
    a new episode) restates its facts: that is the same source, not another one — it must not add evidence."""
    if not source.startswith(("http://", "https://")):
        return False
    others = [u for u in fact.get("sources") or [] if u != episode_uid]
    return any(ep.get("source") == source for ep in await kgdb.get_episodes(db, domain, others))


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
        unread = set()   # statements whose check never ran: asked again next time
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
            verdicts = await judge(st["fact"], "a stored statement",
                                   [f"{names[s]} {rel.replace('_', ' ')} {names[tg]}." for s, rel, tg in triples])
            if verdicts is None:
                unread.add(st["uid"])
                skipped["not checked (model failed)"] += len(triples)
                continue
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
                if uid is None:   # an endpoint was deleted meanwhile
                    added.pop()
                    skipped["entity gone"] += 1
                    continue
                await db.cypher(domain, "MATCH ()-[r:Fact {uid: $u}]->() SET r.sources = $src, r.evidence = $n",
                                u=uid, src=sources, n=max(len(sources), 1))
                for ep in sources:
                    await kgdb.link_mentions(db, domain, ep, [s, tg])
        if not dry_run:
            await db.cypher(domain, "MATCH ()-[r:has_state]->() WHERE r.uid IN $u SET r.relinked = true",
                            u=[st["uid"] for st, _ in part if st["uid"] not in unread])
    return {"dry_run": dry_run, "statements": len(asks), "proposed": proposed, "added": len(added),
            "skipped": dict(skipped), "facts": added}


async def recheck_superseded(db: ArcadeDB, domain: str, dry_run: bool = False, batch: int = 10) -> dict:
    """Supersessions asked of the Linker again — each new fact beside the facts it replaced — and the ones it makes no
    more are undone: that old fact is valid again. Until 2026-10-28 every state of an entity was a candidate for each
    new one, and most of what that superseded was still true (kgdb.fact_candidates). Each superseded fact is asked
    once (`rechecked`); a batch the model fails on is asked next time. One kept as a restatement stays superseded."""
    await kgdb.ensure_domain_db(db, domain)
    rows = await db.cypher(domain, "MATCH ()-[r:Fact]->() WHERE r.invalid_reason STARTS WITH 'superseded by f_' "
                                   "AND r.rechecked IS NULL RETURN r.uid AS uid, r.fact AS fact, r.invalid_reason AS why")
    by_new: dict[str, list[dict]] = {}
    for r in rows:
        if m := re.match(r"superseded by (f_\w+)", r["why"]):
            by_new.setdefault(m[1], []).append(r)
    news = {f["uid"]: f for f in await kgdb.get_facts(db, domain, list(by_new))}
    asks = [(news[u], olds) for u, olds in by_new.items() if u in news]
    restored, kept, unread = [], 0, 0
    for i in range(0, len(asks), batch):
        part = asks[i:i + batch]
        links = await link_or_none([{"fact": new["fact"], "candidates": olds} for new, olds in part])
        if links is None:
            unread += sum(len(olds) for _, olds in part)
            continue
        back = []
        for (new, olds), lk in zip(part, links):
            for old in olds:
                if old["uid"] in lk.supersedes or old["uid"] == lk.same_as:
                    kept += 1
                else:
                    back.append(old["uid"])
                    restored.append({"uid": old["uid"], "fact": old["fact"], "was_superseded_by": new["fact"]})
        if dry_run:
            continue
        await db.cypher(domain, "MATCH ()-[r:Fact]->() WHERE r.uid IN $u "
                                "SET r.invalid_at = null, r.invalid_reason = null", u=back)
        await db.cypher(domain, "MATCH ()-[r:Fact]->() WHERE r.uid IN $u SET r.rechecked = true",
                        u=[old["uid"] for _, olds in part for old in olds])
    return {"dry_run": dry_run, "superseded": len(rows), "restored": len(restored), "kept": kept,
            "not_checked": len(rows) - len(restored) - kept, "model_failed": unread, "facts": restored}


async def resolve_review(db: ArcadeDB, domain: str, review_uid: str, approve: bool, note: str = "") -> dict:
    """Apply (approve) or dismiss (reject) a pending review item. Raises ValueError if it can't."""
    item = await kgdb.get_review(db, domain, review_uid)
    if not item:
        raise ValueError(f"Review item {review_uid} not found")
    if item["status"] != "pending":
        raise ValueError(f"Review item {review_uid} is already {item['status']}")
    p = item["payload"]
    outcome, created = "dismissed", None
    if approve and item["kind"] == "fact":
        [vector] = await embed([p["fact"]])
        created = await kgdb.create_fact(db, domain, p["source_uid"], p["relation"], p["target_uid"], p["fact"],
                                         vector, p["episode_uid"], p["reliability"])
        if created is None:
            # Its entity was merged away or pruned since: approving can't place the fact. The review stays pending.
            raise ValueError(f"An entity of review item {review_uid} no longer exists — reject this review")
        outcome = f"fact {created} added"
    elif approve and item["kind"] == "merge":
        found = {e["uid"] for e in await kgdb.get_entities(db, domain, [p["entity_uid"], p["candidate_uid"]])}
        if len(found) < 2:
            # Merging into a deleted entity would delete the other one with all its facts.
            raise ValueError(f"An entity of review item {review_uid} no longer exists — reject this review")
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
    if not await kgdb.close_review(db, domain, review_uid, "approved" if approve else "rejected", resolution):
        # Someone decided it meanwhile (the Head's sweep and a person at once): theirs stands, not a second copy.
        if created:
            await kgdb.invalidate_fact(db, domain, p["relation"], created, f"review {review_uid} decided twice")
        raise ValueError(f"Review item {review_uid} was already decided")
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


SEARCH = """You prepare a search of an English knowledge graph for a question. Answer in exactly two lines:
QUESTION: the question in English (as it is, when it already is English), keeping every name, term and number
PASSAGE: two or three plain sentences that would answer it, stated the way a knowledge graph's facts are — naming
the things, processes and numbers involved. It is only used to find facts and is never shown: a best guess is fine."""
_searches: dict[tuple[str, str], list[str]] = {}   # (domain, key) → what else a question is searched by


async def _search_texts(db: ArcadeDB, domain: str, question: str) -> list[str]:
    """What a question is searched by besides itself. Its English, when most of it isn't in Latin script — the graph
    is kept in English (agents/extractor.py), and the multilingual embedding matches poorly across languages: measured
    2026-10-28, a Korean exam question's nearest facts were about HBM4 in general (similarity 0.52) while its English
    twin found the answer (0.77). And a passage that would answer it (HyDE, Gao et al. 2022): facts read like answers,
    not like questions. Nothing when the model can't be reached (the question alone is used). With the facts around
    ranked by closeness (_query), tried on the practice set (core/exam.py) on 2026-10-29: 24 → 37, four questions up
    — answered from facts the graph held but never showed — one down.
    Kept in the domain's database (SearchText), not only in memory: each restart wrote them anew, and a question
    of the exam could find other facts than at its last sitting with the graph unchanged (2026-10-29)."""
    key = hashlib.sha1(f"{SEARCH}\n{question}".encode()).hexdigest()[:16]   # a new prompt searches anew
    if (domain, key) not in _searches:
        kept = await kgdb.find_docs(db, domain, "SearchText", ("texts",), "key = :key", order="created_at ASC",
                                    limit=1, key=key)
        if kept:
            texts = kept[0]["texts"]
        else:
            agent = Agent(model_for("searcher"), name="search_texts", system_prompt=SEARCH,
                          model_settings={**model_settings("searcher"), "temperature": 0.0})   # one question, one search
            try:
                out = str((await agent.run(question)).output).strip()
            except Exception as e:   # a search must not fail over its preparation
                print(f"[KG] query searched as asked ({type(e).__name__})", flush=True)
                return []
            english = re.search(r"QUESTION:\s*(.+)", out)
            passage = re.search(r"PASSAGE:\s*(.+)", out, re.S)
            if not english and not passage:
                english = re.match(r"(.+)", out, re.S)   # the question alone, translated
            found = [" ".join(m[1].split())[:600] for m in (english, passage) if m]
            texts = [t for t in dict.fromkeys(found) if t and t != question]
            try:
                await kgdb.insert_doc(db, domain, "SearchText", {"key": key, "question": question[:2000],
                                                                 "texts": texts}, ("texts",))
            except Exception as e:   # kept in memory all the same
                print(f"[KG] search texts not kept ({type(e).__name__})", flush=True)
        if len(_searches) >= 500:
            _searches.pop(next(iter(_searches)))
        _searches[(domain, key)] = texts
    return _searches[(domain, key)]


def _closest(hits: list[list[dict]], k: int) -> list[dict]:
    """The k closest of several searches' hits, each once, at its best similarity."""
    best: dict[str, dict] = {}
    for h in (h for found in hits for h in found):
        if h["uid"] not in best or h["similarity"] > best[h["uid"]]["similarity"]:
            best[h["uid"]] = h
    return sorted(best.values(), key=lambda h: -h["similarity"])[:k]


async def _query(db: ArcadeDB, domain: str, question: str, k: int, empty: dict) -> dict:
    vectors = await embed([question, *await _search_texts(db, domain, question)])
    entities = _closest([await kgdb.search_entities(db, domain, v, k) for v in vectors], k)
    hit_facts = _closest([await kgdb.search_facts(db, domain, v, k) for v in vectors], k)
    seeds = {e["uid"] for e in entities} | {u for f in hit_facts for u in (f["source_uid"], f["target_uid"])}
    # The facts around them, closest to the question first: strongest first, the same hub facts ("the Big Three
    # prioritized HBM allocation") filled 7 of the 15 an exam question was shown, whatever it asked (2026-10-29).
    around = await kgdb.expand(db, domain, sorted(seeds), limit=AROUND, embeddings=True)
    for f in around:
        v = f.pop("embedding", None)
        f["similarity"] = round(max(kgdb.cosine(v, q) for q in vectors), 4) if v else 0.0
    around.sort(key=lambda f: -f["similarity"])

    facts, seen = [], set()
    for f in hit_facts + around:
        if f["uid"] not in seen:
            seen.add(f["uid"])
            facts.append(f)
    known = {e["uid"] for e in entities}
    endpoints = {u for f in facts for u in (f["source_uid"], f["target_uid"])} - known
    entities += await kgdb.get_entities(db, domain, sorted(endpoints))
    # The sources of the first (closest, then strongest) facts first — sorting the uids picked 30 at random.
    sources = list(dict.fromkeys(s for f in facts[:20] for s in (f.get("sources") or [])))[:30]
    rank = {u: i for i, u in enumerate(sources)}
    episodes = sorted(await kgdb.get_episodes(db, domain, sources), key=lambda ep: rank.get(ep["uid"], len(rank)))
    return {**empty, "entities": entities, "facts": facts, "episodes": episodes}
