"""The synthesis — the brain's upper layer: a living review of each core question of the research charter.

    gather   Graph RAG for the core question (kg.query): its valid facts, strongest around it, each with how many
             sources and of which kind (core/sources.py), and the facts that were superseded around the same entities
             (what changed) — forgotten ones are no debate
    write    the reviewer writes from those numbered facts only: the current answer, what confirmed sources agree on,
             where they disagree or what changed, and what the graph can't answer yet. Every sentence cites its facts
             ([2][5], [S1] for a superseded one); a point citing none is dropped — the review holds nothing the graph
             doesn't
    sources  the key sources are counted from what the points cite — papers and official sources first — never chosen
             by the model
    fresh    rewritten only when its facts changed (a mark of their uids and weights) — after each learning cycle and
             on demand

The lower layer stays the truth: facts with their sources. This one is how a person reads it. Stored as the Setting
document "synthesis": core number → page.
"""

import asyncio
import hashlib
import re
import time

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior

from core import activity, charter, kg, learning, sources
from core import database as kgdb
from core.database import ArcadeDB, now
from core.llm import HANGUL, foreign_script, model_for, model_settings

MODEL = model_for("summarizer")
FACTS = 30            # valid facts a review is written from
SUPERSEDED = 10       # superseded facts shown beside them
KEY_SOURCES = 8
TIER_RANK = {t: i for i, t in enumerate(sources.CAPS)}

_running: set[str] = set()
_tasks: set[asyncio.Task] = set()


class SynthesisError(ValueError):
    pass


class Review(BaseModel):
    summary: str = Field(description="The current answer to the core question in two to four sentences, each citing "
                                     "its facts like [2][5]")
    consensus: list[str] = Field(default_factory=list, description="What confirmed facts agree on — one point each, "
                                                                   "citing its facts")
    debates: list[str] = Field(default_factory=list, description="Where sources disagree or what changed — one point "
                                                                 "each, citing its facts ([S1] for superseded ones)")
    open: list[str] = Field(default_factory=list, description="What the facts don't answer yet — short questions")


PROMPT = """You write the living review of one core question of the "{domain}" knowledge domain, from its knowledge
graph's numbered FACTS only — nothing from your own memory.
Core question: {core}
The user wants: {goal}
Depth: {depth}

Write:
- summary: the current answer, two to four sentences
- consensus: what well-confirmed facts agree on — those with several sources, or from papers and official sources,
  first; one point per item
- debates: where facts disagree, or what changed (the superseded facts, marked S) — one point per item; none if none
- open: what the facts don't answer yet, as short questions
Every summary sentence and every point ends with the numbers of the facts it rests on, like [2][5] or [S1]. Say how sure
in words: "confirmed by several sources", "one paper", "a single report". Write in {language}."""

_CITE = re.compile(r"\[(S?)(\d+)\]|\((S)(\d+)\)")    # [2], [S1] — and (S1), as the model writes it at times
_CITE_SPACED = re.compile(r"\s*(?:\[S?\d+\]|\(S\d+\))")


def running(domain: str) -> bool:
    return domain in _running


# ── Gathering ──────────────────────────────────────────────────────────────

async def _superseded(db: ArcadeDB, domain: str, entity_uids: list[str]) -> list[dict]:
    """Facts superseded around these entities, newest first — not the forgotten ones (no source ever confirmed them:
    that is no debate)."""
    if not entity_uids:
        return []
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NOT NULL AND (a.uid IN $u OR b.uid IN $u) "
        "RETURN r.uid AS uid, r.fact AS fact, r.invalid_at AS invalid_at, r.invalid_reason AS reason, "
        "r.sources AS sources ORDER BY r.invalid_at DESC LIMIT $n",
        u=entity_uids, n=SUPERSEDED * 3)
    return [r for r in rows if not (r.get("reason") or "").startswith(learning.FORGOTTEN)][:SUPERSEDED]


async def gather(db: ArcadeDB, domain: str, core: str) -> dict:
    """The evidence for one core question: {facts, superseded, episodes (uid → episode with tier), mark}."""
    found = await kg.query(db, domain, core, 10)
    facts = [f for f in found["facts"] if f.get("valid", True)][:FACTS]
    entities = list(dict.fromkeys(u for f in facts for u in (f["source_uid"], f["target_uid"])))
    old = await _superseded(db, domain, entities[:40])
    uids = sorted({s for f in facts + old for s in f.get("sources") or []})
    episodes = {ep["uid"]: ep for ep in await kgdb.get_episodes(db, domain, uids)}
    mark = hashlib.sha1(("|".join(sorted(f"{f['uid']}:{f['weight']:.2f}" for f in facts))
                         + "|" + "|".join(sorted(f["uid"] for f in old)) + "|" + core).encode()).hexdigest()[:16]
    return {"facts": facts, "superseded": old, "episodes": episodes, "mark": mark}


def _kinds(f: dict, episodes: dict) -> str:
    tiers = [episodes[u]["tier"] for u in f.get("sources") or [] if u in episodes]
    n = len(f.get("sources") or [])
    kinds = ", ".join(sorted(set(tiers), key=TIER_RANK.get))
    return f"{n} source{'s' if n != 1 else ''}" + (f": {kinds}" if kinds else "")


def _numbered(ev: dict) -> str:
    lines = [f"[{i + 1}] {f['fact']} ({_kinds(f, ev['episodes'])})" for i, f in enumerate(ev["facts"])]
    lines += [f"[S{i + 1}] SUPERSEDED {str(f.get('invalid_at') or '')[:10]}: {f['fact']}"
              + (f" — {f['reason'][:120]}" if f.get("reason") else "") for i, f in enumerate(ev["superseded"])]
    return "\n".join(lines)


# ── Writing ────────────────────────────────────────────────────────────────

def _cited(text: str, ev: dict) -> tuple[str, list[str]]:
    """A sentence and the fact uids it cites (citations removed from the text; numbers out of range ignored)."""
    uids = []
    for m in _CITE.finditer(text):
        s, n = (m[1], m[2]) if m[2] else (m[3], m[4])
        pool = ev["superseded"] if s else ev["facts"]
        if 1 <= int(n) <= len(pool):
            uids.append(pool[int(n) - 1]["uid"])
    return _CITE_SPACED.sub("", text).strip(), list(dict.fromkeys(uids))


def _points(items: list[str], ev: dict) -> list[dict]:
    """Points that cite a fact; the rest are dropped — nothing the graph doesn't hold."""
    out = []
    for item in items:
        text, uids = _cited(item, ev)
        if text and uids:
            out.append({"text": text, "facts": uids})
    return out


def _key_sources(cited: set[str], ev: dict) -> list[dict]:
    """The sources behind the cited facts — papers and official sources first, then the most cited."""
    by_uid = {f["uid"]: f for f in ev["facts"] + ev["superseded"]}
    counts: dict[str, int] = {}
    for uid in cited:
        for s in by_uid.get(uid, {}).get("sources") or []:
            if s in ev["episodes"]:
                counts[s] = counts.get(s, 0) + 1
    ranked = sorted(counts, key=lambda s: (TIER_RANK.get(ev["episodes"][s]["tier"], 99), -counts[s]))
    return [{"title": ev["episodes"][s].get("title") or ev["episodes"][s]["source"], "url": ev["episodes"][s]["source"],
             "tier": ev["episodes"][s]["tier"], "facts": counts[s]} for s in ranked[:KEY_SOURCES]]


async def _write(domain: str, core: str, c: dict, ev: dict) -> Review:
    korean = bool(HANGUL.search(core))

    def checked(r: Review) -> Review:
        written = " ".join([r.summary, *r.consensus, *r.debates, *r.open])
        if korean and not HANGUL.search(r.summary):
            raise ModelRetry("The core question is in Korean: write the review in Korean.")
        if foreign := foreign_script(core, written):
            raise ModelRetry(f"Write it in Korean only — no Chinese characters or Japanese ({foreign}).")
        if not _CITE.search(r.summary):
            raise ModelRetry("End every summary sentence with the numbers of the facts it rests on, like [2][5].")
        return r

    prompt = PROMPT.format(domain=domain, core=core, goal=c["goal"], depth=c.get("depth") or "working",
                           language="Korean" if korean else "the core question's language")
    request = f"FACTS:\n{_numbered(ev)}" + ("\n\nWrite the review in Korean (한국어로)." if korean else "")

    async def run(validated: bool) -> Review:
        agent = Agent(MODEL, name="synthesis", model_settings=model_settings("summarizer"), output_type=Review,
                      retries=2 if validated else 4, system_prompt=prompt)
        if validated:
            agent.output_validator(checked)
        return (await agent.run(request)).output
    try:
        return await run(True)
    except UnexpectedModelBehavior as e:   # one review's wording must not stop the others
        print(f"[Synthesis] {domain}: taken unchecked ({e})", flush=True)
        return _scrubbed(core, await run(False))


def _scrubbed(core: str, r: Review) -> Review:
    """A review taken unchecked, without the sentences and points still in a foreign script (a local model's 让 in
    Korean, 2026-10-28) — left out as uncited ones are."""
    def ok(text: str) -> bool:
        return not foreign_script(core, text)
    sentences = re.split(r"(?<=[.!?])\s+", r.summary.strip())
    return Review(summary=" ".join(s for s in sentences if ok(s)), consensus=[p for p in r.consensus if ok(p)],
                  debates=[p for p in r.debates if ok(p)], open=[q for q in r.open if ok(q)])


async def _page(domain: str, n: int, core: str, c: dict, ev: dict) -> dict:
    if not ev["facts"]:
        return {"core": n, "question": core, "summary": "", "consensus": [], "debates": [], "open": [core],
                "sources": [], "facts": 0, "mark": ev["mark"], "written_at": now()}
    with activity.working(domain, "summarizer", f"synthesis {n}: {core[:60]}"):
        r = await _write(domain, core, c, ev)
    summary, cited = _cited(r.summary, ev)
    consensus, debates = _points(r.consensus, ev), _points(r.debates, ev)
    all_cited = set(cited) | {u for p in consensus + debates for u in p["facts"]}
    return {"core": n, "question": core, "summary": summary, "summary_facts": cited, "consensus": consensus,
            "debates": debates, "open": [" ".join(q.split()) for q in r.open if q.strip()][:8],
            "sources": _key_sources(all_cited, ev), "facts": len(ev["facts"]), "mark": ev["mark"], "written_at": now()}


# ── Storage ────────────────────────────────────────────────────────────────

async def _doc(db: ArcadeDB, domain: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key="synthesis")
    return rows[0] if rows else None


async def pages(db: ArcadeDB, domain: str) -> list[dict]:
    """The review's pages, in the charter's order — those of core questions it no longer has left out."""
    doc = await _doc(db, domain) if await db.exists(domain) else None
    stored = ((doc or {}).get("value") or {}).get("pages") or {}
    c = (await charter.get(db, domain))["charter"]
    if not c:
        return []
    return [stored[q] for q in c["questions"] if q in stored]


async def _save(db: ArcadeDB, domain: str, written: dict[str, dict]) -> None:
    doc = await _doc(db, domain)
    stored = ((doc or {}).get("value") or {}).get("pages") or {}
    value = {"pages": {**stored, **written}, "updated_at": now()}
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "synthesis", "value": value}, ("value",))


async def refresh(db: ArcadeDB, domain: str, force: bool = False) -> dict:
    """Rewrite the pages whose facts changed (all with `force`). {written, unchanged, seconds}. SynthesisError without
    a charter; RuntimeError while another refresh of the domain runs."""
    c = (await charter.get(db, domain))["charter"]
    if not c:
        raise SynthesisError("The synthesis follows the research charter's core questions — there is none yet")
    if domain in _running:
        raise RuntimeError(f"The synthesis of {domain} is being written already")
    _running.add(domain)
    started = time.monotonic()
    try:
        doc = await _doc(db, domain)
        stored = ((doc or {}).get("value") or {}).get("pages") or {}
        written, unchanged = {}, 0
        for n, core in enumerate(c["questions"], start=1):
            ev = await gather(db, domain, core)
            if not force and (stored.get(core) or {}).get("mark") == ev["mark"]:
                unchanged += 1
                continue
            try:
                page = await _page(domain, n, core, c, ev)
            except Exception as e:   # one page's trouble leaves its old version, not the review unwritten
                print(f"[Synthesis] {domain}: core question {n} failed: {type(e).__name__}: {e}"[:300], flush=True)
                continue
            written[core] = page
            await _save(db, domain, {core: page})   # each page as it is done: a long refresh shows progress
        return {"written": len(written), "unchanged": unchanged, "seconds": round(time.monotonic() - started)}
    finally:
        _running.discard(domain)


def start(db: ArcadeDB, domain: str, force: bool = False) -> bool:
    if domain in _running:
        return False

    async def run() -> None:
        try:
            r = await refresh(db, domain, force)
            print(f"[Synthesis] {domain}: {r}", flush=True)
        except Exception as e:
            print(f"[Synthesis] {domain} failed: {type(e).__name__}: {e}", flush=True)

    task = asyncio.get_running_loop().create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True
