"""The crew — the Head Agent's team, and how they talk: work goes out on a queue, reports come back on threads.

    Head → agent   assign(agent, task, inputs): a task on a thread, its inputs checked against the task's model;
                   ask(agent, question): a question, answered from what that agent knows right now (its desk)
    agent → Head   a finished task is reported on its thread and the Head is woken to read it and decide (HEAD_WAKE);
                   an agent with news of its own (a triggered playbook, the morning report) leaves a notice
    the graph      gets FACTS only. Findings are stored through the pipeline — extracted, then validated against
                   their source — by the Head's ingest_data or a research / learn task; a conversation never is.

Threads and messages live in the domain's database (Thread, Message), so they follow the domain's access rules and
the UI shows who asked whom what, and what came back. One queue for every domain, worked one message at a time (the
agents share one local model); long jobs (a scout, a research mission) run in the background and report when done.
A restart re-queues what was waiting and reports what it interrupted.

Runaway guard: one thread wakes the Head at most MAX_WAKES times and a domain at most WAKES_PER_HOUR times an hour;
past that, reports wait in the inbox for the next conversation.
"""

import asyncio
import json
import os
import re
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import get_args, get_origin

import httpx
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent

from agents.ontologist import generate_ontology
from agents.validator import validate
from core import actions, auth, catalog, coding, digest, kg, playbooks, sensors
from core import database as kgdb
from core.database import ArcadeDB, now
from core.llm import model_for, model_settings
from core.ontology import Ontology
from domains import registry

HERMES_URL = os.getenv("SOVEREIGN_HERMES_URL", "http://localhost:8090").rstrip("/")
RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
HEAD_WAKE = os.getenv("HEAD_WAKE", "on").strip().lower() not in ("0", "off", "false", "no")
MAX_WAKES = int(os.getenv("HEAD_MAX_WAKES", "4"))           # per thread
WAKES_PER_HOUR = int(os.getenv("HEAD_WAKES_PER_HOUR", "12"))  # per domain
WAKE_SECONDS = 1200        # a woken Head may ingest (minutes of pipeline)
JOB_POLL = 10              # seconds between looks at a background job
JOB_LIMIT = 3600           # a background job is given up after this long
DESK_CHARS = 6000
HISTORY = 8                # earlier messages of a thread a consulted agent sees

MESSAGE_JSON = ("data",)
HEAD = "head"
OPEN, WORKING, DONE, FAILED = "open", "working", "done", "failed"
QUEUED, WAITING, UNREAD, READ = "queued", "waiting", "unread", "read"


class CrewError(ValueError):
    """A request the crew can't take — the message says what would work."""


@dataclass
class Outcome:
    text: str
    data: dict = field(default_factory=dict)
    ok: bool = True


@dataclass
class Task:
    name: str
    description: str
    inputs: type[BaseModel]
    run: Callable[[ArcadeDB, str, BaseModel], Awaitable[Outcome]]
    background: bool = False   # long: runs beside the queue, reports when done


@dataclass
class Member:
    name: str
    title: str
    role: str
    desk: Callable[[ArcadeDB, str], Awaitable[str]]
    tasks: dict[str, Task] = field(default_factory=dict)

    def card(self) -> dict:
        return {"name": self.name, "title": self.title, "role": self.role,
                "tasks": [{"name": t.name, "description": t.description, "background": t.background,
                           "inputs": _fields(t.inputs), "example": _example(t.inputs),
                           "optional": [n for n, f in t.inputs.model_fields.items() if not f.is_required()]}
                          for t in self.tasks.values()]}


def _kind(annotation) -> str:
    if get_origin(annotation) is list:
        return f"list of {_kind(get_args(annotation)[0])}s"
    return {str: "text", int: "number", float: "number", bool: "true/false"}.get(annotation, "value")


def _fields(model: type[BaseModel]) -> dict:
    """name → "(type) description [optional]" — what a task takes, for the Head and for error messages."""
    return {name: f"({_kind(f.annotation)}) {f.description or ''}"
                  + ("" if f.is_required() else f" — optional, default {f.default!r}")
            for name, f in model.model_fields.items()}


def _example(model: type[BaseModel]) -> dict:
    """The required inputs as a JSON example: {"claims": ["…"], "source_text": "…"}."""
    sample = {"text": "…", "number": 24, "true/false": True}
    return {name: (["…"] if get_origin(f.annotation) is list else sample.get(_kind(f.annotation), "…"))
            for name, f in model.model_fields.items() if f.is_required()}


# What a model writes for a task is made into the task's fields where the meaning is unambiguous — then the task's
# model checks it as strictly as ever. Measured (qwen3.6:35b): the whole call written into `task`
# ("check(claims=[…])"), the inputs sent as a JSON string, list items as {"text": …} objects.
_TASK_NAME = re.compile(r"[A-Za-z_][\w.]*")
_TEXT_KEYS = ("text", "claim", "statement", "value", "content", "question", "need")


def _task_name(task: str) -> str:
    m = _TASK_NAME.match((task or "").strip())
    return m[0].rsplit(".", 1)[-1].lower() if m else ""


def _text(v):
    if isinstance(v, dict):
        for k in _TEXT_KEYS:
            if isinstance(v.get(k), str):
                return v[k]
        texts = [x for x in v.values() if isinstance(x, str)]
        return texts[0] if len(texts) == 1 else v
    return v


def _shape(model: type[BaseModel], raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            required = [n for n, f in model.model_fields.items() if f.is_required()]
            raw = {required[0]: raw} if len(required) == 1 else {}
    out = dict(raw) if isinstance(raw, dict) else {}
    for name, f in model.model_fields.items():
        if name not in out:
            continue
        if get_origin(f.annotation) is list and get_args(f.annotation) == (str,):
            v = [out[name]] if isinstance(out[name], str) else out[name]
            out[name] = [_text(x) for x in v] if isinstance(v, list) else v
        elif f.annotation is str:
            out[name] = _text(out[name])
    return out


# ── Talking to the other services ──────────────────────────────────────────

async def _research(domain: str, method: str, path: str, **kw) -> dict:
    async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=30, headers=auth.headers(domain)) as client:
        r = await client.request(method, path, **kw)
    r.raise_for_status()
    return r.json()


async def _ask_head(domain: str, message: str) -> str:
    async with httpx.AsyncClient(base_url=HERMES_URL, timeout=WAKE_SECONDS, headers=auth.headers(domain)) as client:
        r = await client.post(f"/domains/{domain}/ask", json={"message": message, "max_iterations": 12})
    r.raise_for_status()
    return r.json().get("answer") or ""


def _clip(text: str, n: int = DESK_CHARS) -> str:
    return text if len(text) <= n else text[:n] + "\n…"


async def _poll(check: Callable[[], Awaitable[dict | None]], finished: Callable[[dict], bool]) -> dict:
    """Wait for a background job; its last state (also after JOB_LIMIT, or when it disappeared)."""
    deadline, last = time.monotonic() + JOB_LIMIT, {}
    while time.monotonic() < deadline:
        state = await check()
        if state is None:
            return {**last, "status": "lost", "error": "the job is gone (the service restarted)"}
        last = state
        if finished(state):
            return state
        await asyncio.sleep(JOB_POLL)
    return {**last, "status": "timeout", "error": f"still running after {JOB_LIMIT // 60} minutes"}


# ── Tasks ──────────────────────────────────────────────────────────────────

class Investigate(BaseModel):
    question: str = Field(min_length=8, description="What to find out on the web")


async def _investigate(db: ArcadeDB, domain: str, x: Investigate) -> Outcome:
    job = await _research(domain, "POST", f"/domains/{domain}/research", json={"mode": "mission", "question": x.question})

    async def check():
        try:
            return await _research(domain, "GET", f"/jobs/{job['id']}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
    j = await _poll(check, lambda s: s.get("status") in ("done", "failed"))
    pages = j.get("pages") or []
    read = sum(1 for p in pages if p.get("status") not in ("failed", "skipped"))
    summary = j.get("summary") or {}
    if j.get("status") != "done":
        return Outcome(f"Research on “{x.question}” {j.get('status')}: {j.get('error') or 'no detail'}",
                       {"job": job["id"], "status": j.get("status")}, ok=False)
    stored = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in summary.items() if isinstance(v, int) and v) or "—"
    return Outcome(f"Researched “{x.question}”: read {read} of {len(pages)} pages; stored in the graph: {stored}.\n"
                   f"{(j.get('report') or '').strip()[:1500]}", {"job": job["id"], "summary": summary})


class SensorNeed(BaseModel):
    need: str = Field(min_length=8, description="The recurring live-data need, e.g. 'latest price of a stock ticker'")


async def _sensor_job(db: ArcadeDB, domain: str, need: str, backend: str) -> Outcome:
    j = coding.submit(db, domain, need, backend=backend)

    async def check():
        return coding.job(j["id"])
    j = await _poll(check, lambda s: s["status"] in ("done", "failed"))
    if j["status"] != "done":
        return Outcome(f"No sensor for “{need}”: {j.get('error') or j['status']}", {"request": j.get("id")}, ok=False)
    what = j.get("note") or (f"sensor {j['sensor']} stored" if j.get("sensor") else "done")
    return Outcome(f"Sensor request “{need}”: {what}", {"request": j["id"], "sensor": j.get("sensor"),
                                                       "seconds": j.get("seconds")})


async def _find_sources(db: ArcadeDB, domain: str, x: SensorNeed) -> Outcome:
    return await _sensor_job(db, domain, x.need, "scout")


async def _write_sensor(db: ArcadeDB, domain: str, x: SensorNeed) -> Outcome:
    return await _sensor_job(db, domain, x.need, "builtin")


class Learn(BaseModel):
    text: str = Field(min_length=40, description="The source text to learn from (an article, notes, a finding)")
    source: str = Field("head", description="Where it came from: a URL or a short label")
    title: str = Field("", description="A title for the source")


async def _learn(db: ArcadeDB, domain: str, x: Learn) -> Outcome:
    r = await kg.ingest(db, domain, x.text, source=x.source, title=x.title or None)
    if r.get("duplicate"):
        return Outcome("Already learned: the same text is in the graph.", {"episode_uid": r["episode_uid"]})
    keys = ("entities_created", "entities_matched", "facts_created", "facts_strengthened", "facts_invalidated",
            "facts_rejected", "review_items")
    counts = {k: r[k] for k in keys}
    return Outcome("Learned it: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()) +
                   " (facts were checked against the text by the Validator; doubtful ones wait in reviews).",
                   {"episode_uid": r["episode_uid"], **counts, "touched_uids": r["touched_uids"]})


class Check(BaseModel):
    claims: list[str] = Field(min_length=1, max_length=20, description="Statements to check")
    source_text: str = Field(min_length=20, description="The text to check them against")
    source: str = Field("", description="Where the text came from")


async def _check(db: ArcadeDB, domain: str, x: Check) -> Outcome:
    verdicts = await validate(x.source_text, x.source or "given text", x.claims)
    by_index = {v.index: v for v in verdicts}
    lines = []
    for i, claim in enumerate(x.claims):
        v = by_index.get(i)
        lines.append(f"- {claim}: " + ("not judged" if v is None else
                     f"{'supported' if v.supported else 'NOT supported'} (reliability {v.reliability:.2f})"
                     + (f" — {v.reason}" if v.reason else "")))
    return Outcome("\n".join(lines), {"verdicts": [v.model_dump() for v in verdicts]})


class Propose(BaseModel):
    reason: str = Field("", description="What the current ontology seems to miss")


async def _propose(db: ArcadeDB, domain: str, x: Propose) -> Outcome:
    dom = registry.load_domain(domain)
    description = dom["config"].get("description") or domain
    current = Ontology.model_validate(dom["ontology"])
    rows = await db.cypher(domain, "MATCH (ep:Episode) RETURN ep.title AS title, left(ep.content, 1500) AS text "
                                   "ORDER BY ep.created_at DESC LIMIT 12")
    corpus = [f"{r.get('title') or ''}\n{r.get('text') or ''}" for r in rows]
    if x.reason:
        corpus.insert(0, f"What seems missing: {x.reason}")
    new = await generate_ontology(domain, description, corpus)

    def diff(old: list[str], fresh: list[str]) -> str:
        added, dropped = sorted(set(fresh) - set(old)), sorted(set(old) - set(fresh))
        return f"+{', '.join(added) or '—'} / −{', '.join(dropped) or '—'}"
    return Outcome(f"Proposed ontology (not applied — update_ontology applies it): entity types "
                   f"{diff(current.entity_types, new.entity_types)}; relations "
                   f"{diff(current.relation_types, new.relation_types)}.", new.model_dump())


class Nothing(BaseModel):
    pass


async def _relink(db: ArcadeDB, domain: str, x: Nothing) -> Outcome:
    r = await kg.relink_states(db, domain)
    return Outcome(f"Read {r['statements']} facts filed as one entity's state that name another entity: added "
                   f"{r['added']} relations ({r['proposed']} proposed; skipped: {r['skipped'] or 'none'}).",
                   {**{k: r[k] for k in ("statements", "proposed", "added", "skipped")}, "facts": r["facts"][:40]})


async def _refresh_playbooks(db: ArcadeDB, domain: str, x: Nothing) -> Outcome:
    counts = await playbooks.refresh(db, domain)
    active = await playbooks.list_playbooks(db, domain)
    return Outcome(f"Playbooks rewritten from the graph: {counts}. Active now: "
                   + "; ".join(p.get("name") or p["uid"] for p in active[:12]), counts)


class Since(BaseModel):
    hours: int = Field(24, ge=1, le=720, description="How far back to look")


async def _watch(db: ArcadeDB, domain: str, x: Since) -> Outcome:
    since = (datetime.now(timezone.utc) - timedelta(hours=x.hours)).isoformat(timespec="seconds")
    fired = await playbooks.check_triggers(db, domain)
    evaluated = await playbooks.evaluate(db, domain, since)
    proposals = evaluated.get("proposals", []) + fired
    return Outcome(f"Checked {evaluated.get('evaluated', 0)} playbooks against the last {x.hours} h and the live "
                   f"triggers: {len(proposals)} proposed" + ("" if not proposals else " — " + "; ".join(
                       str(p.get("action") or p.get("playbook") or p.get("uid")) for p in proposals[:8])),
                   {"proposals": proposals[:20]})


async def _summarize(db: ArcadeDB, domain: str, x: Since) -> Outcome:
    since = (datetime.now(timezone.utc) - timedelta(hours=x.hours)).isoformat(timespec="seconds")
    r = await digest.refresh_summaries(db, domain, since)
    return Outcome(f"Entity summaries refreshed from their current facts: {r}", r)


async def _brief(db: ArcadeDB, domain: str, x: Since) -> Outcome:
    r = await digest.build_report(db, domain, x.hours)
    return Outcome(f"Report written: {r.get('headline') or r.get('title') or ''}".strip(),
                   {"report": r.get("uid"), "headline": r.get("headline")})


# ── Desks: what each agent knows right now ─────────────────────────────────

async def _desk_research(db: ArcadeDB, domain: str) -> str:
    try:
        jobs = (await _research(domain, "GET", "/jobs", params={"domain": domain, "limit": 10})).get("jobs", [])
    except httpx.HTTPError as e:
        return f"(the research service is unreachable: {type(e).__name__})"
    return "Recent research jobs:\n" + "\n".join(
        f"- {j.get('created_at', '')[:16]} {j.get('mode')} [{j.get('status')}] {j.get('question') or ''} "
        f"→ {j.get('summary') or ''}" for j in jobs) if jobs else "No research jobs yet."


async def _desk_scout(db: ArcadeDB, domain: str) -> str:
    lines = ["Sensors (groups of sources):"]
    for g in await sensors.groups(db, domain):
        srcs = ", ".join(f"{s['host']}({'ok' if s['last_ok'] else 'failing' if s['last_ok'] is False else 'new'}"
                         + (", resting" if s.get("cooldown_until") else "") + ")" for s in g["sources"][:10])
        lines.append(f"- {g['name']}: {g['working']}/{len(g['sources'])} working — {srcs}")
    jobs = coding.jobs(domain)[:6]
    lines.append("Recent sensor requests:" if jobs else "No sensor requests since the last restart.")
    lines += [f"- {j['need']} [{j['status']}, {j['backend']}] {j.get('note') or j.get('error') or ''}"[:300] for j in jobs]
    lines.append(f"Source catalog (shared): {catalog.stats()}")
    return "\n".join(lines)


async def _desk_coder(db: ArcadeDB, domain: str) -> str:
    jobs = [j for j in coding.jobs(domain) if j["backend"] != "scout"][:6]
    return "Sensors I wrote (since the last restart):\n" + "\n".join(
        f"- {j['need']} [{j['status']}] {j.get('sensor') or j.get('error') or ''}"[:300] for j in jobs) \
        if jobs else "I haven't written a sensor since the last restart; the Scout is the default."


async def _desk_extractor(db: ArcadeDB, domain: str) -> str:
    stats = await kgdb.domain_stats(db, domain)
    eps = await kgdb.list_episodes(db, domain, 10)
    return (f"Graph: {stats}\nRecent sources I read:\n" +
            "\n".join(f"- {e.get('created_at', '')[:16]} {e.get('title') or e.get('source')}" for e in eps))


async def _reviews(db: ArcadeDB, domain: str, kind: str, what: str) -> str:
    pending = [r for r in await kgdb.list_reviews(db, domain) if r.get("kind") == kind]
    return f"{len(pending)} {what} waiting for the Head:\n" + "\n".join(f"- {r['summary']}" for r in pending[:12])


async def _desk_validator(db: ArcadeDB, domain: str) -> str:
    stats = await kgdb.domain_stats(db, domain)
    weak = await _reviews(db, domain, "fact", "low-reliability facts")
    rejected = await kgdb.top_facts(db, domain, 8, valid=False)
    return (f"Graph: {stats}\n{weak}\nRecently invalidated facts:\n"
            + "\n".join(f"- {f['fact']} ({f.get('invalid_reason') or 'invalid'})"[:240]
                        for f in rejected))


async def _desk_resolver(db: ArcadeDB, domain: str) -> str:
    return await _reviews(db, domain, "merge", "possible duplicates (same thing, two names?)")


async def _desk_linker(db: ArcadeDB, domain: str) -> str:
    return await _reviews(db, domain, "link", "facts that may restate or replace older ones")


async def _desk_ontologist(db: ArcadeDB, domain: str) -> str:
    ontology = registry.load_domain(domain)["ontology"]
    gaps: dict[str, int] = defaultdict(int)
    rows = await db.cypher(domain, "MATCH (ep:Episode) WHERE ep.ontology_gaps IS NOT NULL RETURN ep.ontology_gaps AS g "
                                   "ORDER BY ep.created_at DESC LIMIT 50")
    for r in rows:
        for k, v in (json.loads(r["g"]) if isinstance(r["g"], str) else r["g"] or {}).items():
            gaps[k] += v
    top = sorted(gaps.items(), key=lambda kv: -kv[1])[:12]
    return (f"Entity types: {', '.join(ontology.get('entity_types', []))}\n"
            f"Relations: {', '.join(ontology.get('relation_types', []))}\n"
            f"What recent sources said that the ontology couldn't hold: "
            + (", ".join(f"{k} ×{v}" for k, v in top) or "nothing"))


async def _desk_strategist(db: ArcadeDB, domain: str) -> str:
    pbs = await playbooks.list_playbooks(db, domain)
    return "Active playbooks:\n" + "\n".join(
        f"- {p.get('name')}: when {p.get('situation') or '…'} → {p.get('action')}"
        + (f" [live trigger: {str(p['trigger'])[:120]}]" if p.get("trigger") else "")
        for p in pbs[:20]) if pbs else "No playbooks yet."


async def _desk_watcher(db: ArcadeDB, domain: str) -> str:
    props = await actions.list_proposals(db, domain, limit=12)
    pbs = await playbooks.list_playbooks(db, domain)
    fired = [p for p in pbs if p.get("last_fired_at")]
    return ("Recent proposals:\n" + "\n".join(f"- {p.get('created_at', '')[:16]} {p.get('action')} [{p.get('status')}] "
                                               f"{(p.get('rationale') or '')[:160]}" for p in props)
            + "\nPlaybooks that fired: " + (", ".join(f"{p.get('name')} at {p['last_fired_at'][:16]}" for p in fired)
                                            or "none yet"))


async def _desk_summarizer(db: ArcadeDB, domain: str) -> str:
    top = await kgdb.top_entities(db, domain, 12)
    return "Most connected entities and their summaries:\n" + "\n".join(
        f"- {e['name']} ({e.get('type')}): {(e.get('summary') or '(none)')[:200]}" for e in top)


async def _desk_reporter(db: ArcadeDB, domain: str) -> str:
    reports = await kgdb.list_reports(db, domain, 3)
    return "Latest reports:\n" + "\n".join(
        f"- {r.get('created_at', '')[:16]} {r.get('headline') or ''}\n  {(r.get('briefing') or '')[:600]}"
        for r in reports) if reports else "No report yet — one is written every night after research."


def _member(name, title, role, desk, *tasks: Task) -> Member:
    return Member(name, title, role, desk, {t.name: t for t in tasks})


ROSTER: dict[str, Member] = {m.name: m for m in [
    _member("research", "Researcher", "reads the web for a question and stores what each page states (DeerFlow)",
            _desk_research, Task("investigate", "Research a question on the web; every page read is learned into "
                                 "the graph (minutes)", Investigate, _investigate, background=True)),
    _member("scout", "Scout", "finds live-data sources for a need and keeps every one that works",
            _desk_scout, Task("find_sources", "Find sources for a recurring live-data need and build a sensor from "
                              "them (10-20 min)", SensorNeed, _find_sources, background=True)),
    _member("coder", "Coder", "writes a single-source sensor module (the Scout's alternative)",
            _desk_coder, Task("write_sensor", "Write one sensor module for a need (minutes)", SensorNeed,
                              _write_sensor, background=True)),
    _member("extractor", "Extractor", "turns source text into entities and facts — the start of the learning "
            "pipeline (then Validator, Resolver, Linker)",
            _desk_extractor, Task("learn", "Learn a text into the graph through the whole pipeline: extract, "
                                  "validate against the text, resolve duplicates, link to existing facts", Learn,
                                  _learn)),
    _member("validator", "Validator", "checks every fact against the source it came from — only supported facts "
            "enter the graph", _desk_validator,
            Task("check", "Check statements against a text (nothing is stored)", Check, _check)),
    _member("resolver", "Resolver", "decides whether a new entity is the same real thing as a known one",
            _desk_resolver),
    _member("linker", "Linker", "finds the existing facts a new fact restates or contradicts, and the relations "
            "hidden in facts filed about one entity", _desk_linker,
            Task("relink", "Turn stored facts about one entity that name another into the relations they state, "
                 "each checked by the Validator (minutes)", Nothing, _relink, background=True)),
    _member("ontologist", "Ontologist", "designs the domain's grammar: its entity types and relations",
            _desk_ontologist, Task("propose", "Propose an ontology from recent sources (not applied)", Propose,
                                   _propose)),
    _member("strategist", "Strategist", "turns what the graph knows into playbooks: if this happens, do that",
            _desk_strategist, Task("refresh_playbooks", "Rewrite the playbooks from the graph now", Nothing,
                                   _refresh_playbooks)),
    _member("watcher", "Watcher", "checks new knowledge and live triggers against the playbooks and proposes actions",
            _desk_watcher, Task("check_now", "Check the playbooks against recent changes and live triggers now",
                                Since, _watch)),
    _member("summarizer", "Summarizer", "rewrites entity summaries from their current facts",
            _desk_summarizer, Task("refresh", "Refresh the summaries of entities that changed", Since, _summarize)),
    _member("reporter", "Reporter", "writes the daily briefing of what changed",
            _desk_reporter, Task("brief_now", "Write a briefing of what changed now", Since, _brief)),
]}


def roster() -> list[dict]:
    return [m.card() for m in ROSTER.values()]


def _member_of(agent: str) -> Member:
    name = re.sub(r"^(the|agent)\s+", "", (agent or "").strip().lower()).removesuffix(" agent")
    if (m := ROSTER.get(name) or next((x for x in ROSTER.values() if x.title.lower() == name), None)) is None:
        raise CrewError(f"No agent '{agent}'. The team: {', '.join(ROSTER)}")
    return m


# ── Threads and messages ───────────────────────────────────────────────────

async def _post(db: ArcadeDB, domain: str, thread: str, sender: str, recipient: str, kind: str, text: str,
                data: dict | None = None, task: str = "", status: str | None = None) -> dict:
    status = status or (QUEUED if recipient in ROSTER else UNREAD)
    msg = await kgdb.insert_doc(db, domain, "Message", {
        "thread": thread, "sender": sender, "recipient": recipient, "kind": kind, "task": task, "text": text,
        "data": data or {}, "status": status, "finished_at": None}, MESSAGE_JSON)
    await kgdb.update_doc(db, domain, "Thread", thread, {"updated_at": msg["created_at"]})
    if status == QUEUED:
        _ready.set()
    return msg


async def _open(db: ArcadeDB, domain: str, agent: str, title: str, by: str, thread: str | None) -> str:
    if thread:
        if not await kgdb.get_doc(db, domain, "Thread", thread):
            raise CrewError(f"No thread '{thread}' in {domain}")
        await kgdb.update_doc(db, domain, "Thread", thread, {"status": OPEN})
        return thread
    t = await kgdb.insert_doc(db, domain, "Thread", {"title": title[:160], "agent": agent, "opened_by": by,
                                                      "status": OPEN, "wakes": 0, "updated_at": now()})
    return t["uid"]


async def assign(db: ArcadeDB, domain: str, agent: str, task: str, inputs: dict | str | None = None,
                 thread: str | None = None, by: str = HEAD, note: str = "") -> dict:
    """Queue a task for an agent → {thread, message, status}. Raises CrewError with what the task takes."""
    registry.load_domain(domain)
    member = _member_of(agent)
    if (t := member.tasks.get(_task_name(task))) is None:
        raise CrewError(f"The {member.title} has no task '{task}'. Its tasks: "
                        + (", ".join(member.tasks) or "none — ask it a question instead")
                        + ". `task` is only the name; the fields go in `inputs`")
    task = t.name
    try:
        checked = t.inputs.model_validate(_shape(t.inputs, inputs))
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'inputs'}: {x['msg']}" for x in e.errors())
        raise CrewError(f"Inputs for {member.name}.{task}: {problems}. Send inputs like "
                        f"{json.dumps(_example(t.inputs), ensure_ascii=False)} — fields: {_fields(t.inputs)}") from e
    shown = next((v for v in checked.model_dump().values() if isinstance(v, str) and v), "")
    uid = await _open(db, domain, member.name, f"{member.title}: {task} — {shown}"[:160], by, thread)
    text = note or (f"{task}: {shown}" if shown else task)
    msg = await _post(db, domain, uid, by, member.name, "task", text, {"inputs": checked.model_dump()}, task=task)
    return {"thread": uid, "message": msg["uid"], "status": QUEUED, "agent": member.name, "task": task,
            "background": t.background, "ahead": await _queue_length(db)}


async def ask(db: ArcadeDB, domain: str, agent: str, question: str, thread: str | None = None, by: str = HEAD,
              wait: float = 0) -> dict:
    """Queue a question; with `wait`, wait that many seconds for the answer → {thread, message, answer?}."""
    registry.load_domain(domain)
    member = _member_of(agent)
    if not question.strip():
        raise CrewError("Empty question")
    uid = await _open(db, domain, member.name, f"{member.title}: {question}"[:160], by, thread)
    msg = await _post(db, domain, uid, by, member.name, "question", question.strip())
    out = {"thread": uid, "message": msg["uid"], "status": QUEUED, "agent": member.name}
    if wait > 0:
        future = asyncio.get_running_loop().create_future()
        _waiters[msg["uid"]] = future
        try:
            answer = await asyncio.wait_for(asyncio.shield(future), wait)
            await kgdb.update_doc(db, domain, "Message", answer["uid"], {"status": READ})
            out.update(status=DONE, answer=answer["text"])
        except asyncio.TimeoutError:
            out["note"] = f"Not answered within {wait:.0f} s — the answer will be in your inbox"
        finally:
            _waiters.pop(msg["uid"], None)
    return out


async def notify(db: ArcadeDB, domain: str, agent: str, text: str, data: dict | None = None,
                 thread: str | None = None) -> dict:
    """An agent's own news for the Head (no wake): it waits in the inbox."""
    member = _member_of(agent)
    uid = await _open(db, domain, member.name, f"{member.title}: {text}"[:160], member.name, thread)
    msg = await _post(db, domain, uid, member.name, HEAD, "notice", text, data)
    await kgdb.update_doc(db, domain, "Thread", uid, {"status": DONE})
    return msg


async def inbox(db: ArcadeDB, domain: str, mark_read: bool = True, limit: int = 20) -> list[dict]:
    """Unread reports, answers and notices for the Head, oldest first."""
    rows = await kgdb.find_docs(db, domain, "Message", MESSAGE_JSON, "recipient = :h AND status = :s",
                                order="created_at ASC", limit=limit, h=HEAD, s=UNREAD)
    if mark_read:
        for r in rows:
            await kgdb.update_doc(db, domain, "Message", r["uid"], {"status": READ})
    return rows


async def unread(db: ArcadeDB, domain: str) -> int:
    if not await db.exists(domain):
        return 0
    await kgdb.ensure_domain_db(db, domain)
    rows = await db.sql(domain, "SELECT count(*) AS n FROM Message WHERE recipient = :h AND status = :s",
                        h=HEAD, s=UNREAD)
    return rows[0]["n"] if rows else 0


async def thread(db: ArcadeDB, domain: str, uid: str) -> dict | None:
    t = await kgdb.get_doc(db, domain, "Thread", uid)
    if not t:
        return None
    t["messages"] = await kgdb.find_docs(db, domain, "Message", MESSAGE_JSON, "thread = :t", order="created_at ASC",
                                         limit=200, t=uid)
    return t


async def threads(db: ArcadeDB, domain: str, agent: str | None = None, limit: int = 40) -> list[dict]:
    where, params = ("agent = :a", {"a": agent}) if agent else ("", {})
    return await kgdb.find_docs(db, domain, "Thread", (), where, order="updated_at DESC", limit=limit, **params)


async def team(db: ArcadeDB, domain: str) -> list[dict]:
    """The roster with each agent's state in this domain: working / queued / idle."""
    open_msgs = await kgdb.find_docs(db, domain, "Message", (), "status IN :states", limit=500,
                                     states=[QUEUED, WORKING, WAITING])
    out = []
    for card in roster():
        mine = [m for m in open_msgs if m["recipient"] == card["name"]]
        state = ("working" if any(m["status"] in (WORKING, WAITING) for m in mine)
                 else "queued" if mine else "idle")
        out.append({**card, "state": state, "queued": sum(m["status"] == QUEUED for m in mine)})
    return out


# ── The queue ──────────────────────────────────────────────────────────────

_ready = asyncio.Event()
_waiters: dict[str, asyncio.Future] = {}
_background: set[asyncio.Task] = set()
_wakes: dict[str, deque] = defaultdict(deque)     # domain → wake times (monotonic)
_waking: set[str] = set()
_consult = Agent(model_for("crew"), name="crew_consult", model_settings=model_settings(), output_type=str, retries=2)


async def _queue_length(db: ArcadeDB) -> int:
    """Messages waiting for an agent, across every domain (this one included)."""
    n = 0
    for domain in registry.list_domains():
        if await db.exists(domain):
            await kgdb.ensure_domain_db(db, domain)
            rows = await db.sql(domain, "SELECT count(*) AS n FROM Message WHERE status = :s", s=QUEUED)
            n += rows[0]["n"] if rows else 0
    return n


async def _next(db: ArcadeDB) -> tuple[str, dict] | None:
    best = None
    for domain in registry.list_domains():
        rows = await kgdb.find_docs(db, domain, "Message", MESSAGE_JSON, "status = :s AND recipient IN :names",
                                    order="created_at ASC", limit=1, s=QUEUED, names=list(ROSTER))
        if rows and (best is None or rows[0]["created_at"] < best[1]["created_at"]):
            best = (domain, rows[0])
    return best


async def _history(db: ArcadeDB, domain: str, msg: dict) -> str:
    earlier = [m for m in await kgdb.find_docs(db, domain, "Message", (), "thread = :t", order="created_at ASC",
                                               limit=200, t=msg["thread"]) if m["uid"] != msg["uid"]]
    return "\n".join(f"{m['sender']} → {m['recipient']} ({m['kind']}): {m['text'][:600]}" for m in earlier[-HISTORY:])


async def _answer(db: ArcadeDB, domain: str, member: Member, msg: dict) -> Outcome:
    desk = _clip(await member.desk(db, domain))
    tasks = ", ".join(f"{t.name} ({t.description})" for t in member.tasks.values()) or "none"
    history = await _history(db, domain, msg)
    prompt = (f"You are the {member.title} of The Sovereign, a team of agents run by the Head Agent. Your job: "
              f"{member.role}. Domain: {domain}.\n\n"
              f"WHAT YOU KNOW RIGHT NOW (your desk):\n{desk}\n\n"
              + (f"THIS THREAD SO FAR:\n{history}\n\n" if history else "")
              + f"QUESTION from {msg['sender']}: {msg['text']}\n\n"
              f"Answer as the {member.title}, in at most five sentences, only from your desk and the thread. If your "
              f"desk doesn't show it, say so and name the task of yours that would find out (your tasks: {tasks}). "
              f"Answer in the language of the question.")
    out = (await _consult.run(prompt)).output
    return Outcome(str(out).strip() or "(no answer)")


async def _work(db: ArcadeDB, domain: str, msg: dict) -> None:
    """One queued message: run the task or answer the question, then report on the thread."""
    member = ROSTER.get(msg["recipient"])
    if member is None:
        await _finish(db, domain, msg, Outcome(f"No agent '{msg['recipient']}'", ok=False))
        return
    await kgdb.update_doc(db, domain, "Message", msg["uid"], {"status": WORKING})
    await kgdb.update_doc(db, domain, "Thread", msg["thread"], {"status": WORKING})
    t = member.tasks.get(msg["task"]) if msg["kind"] == "task" else None

    async def run() -> None:
        try:
            if msg["kind"] == "task":
                if t is None:
                    raise CrewError(f"The {member.title} has no task '{msg['task']}'")
                outcome = await t.run(db, domain, t.inputs.model_validate((msg.get("data") or {}).get("inputs", {})))
            else:
                outcome = await _answer(db, domain, member, msg)
        except Exception as e:
            outcome = Outcome(f"{type(e).__name__}: {e}"[:1000], ok=False)
        await _finish(db, domain, msg, outcome)

    if t is not None and t.background:
        await kgdb.update_doc(db, domain, "Message", msg["uid"], {"status": WAITING})
        task = asyncio.get_running_loop().create_task(run())
        _background.add(task)
        task.add_done_callback(_background.discard)
    else:
        await run()


async def _finish(db: ArcadeDB, domain: str, msg: dict, outcome: Outcome) -> None:
    await kgdb.update_doc(db, domain, "Message", msg["uid"],
                          {"status": DONE if outcome.ok else FAILED, "finished_at": now()})
    kind = "answer" if msg["kind"] == "question" else "report"
    data = {**outcome.data, "ok": outcome.ok, "in_reply_to": msg["uid"]}
    reply = await _post(db, domain, msg["thread"], msg["recipient"], msg["sender"], kind, outcome.text, data,
                        task=msg.get("task") or "")
    pending = await kgdb.find_docs(db, domain, "Message", (), "thread = :t AND status IN :states", limit=1,
                                   t=msg["thread"], states=[QUEUED, WORKING, WAITING])
    if not pending:
        await kgdb.update_doc(db, domain, "Thread", msg["thread"], {"status": DONE if outcome.ok else FAILED})
    if (future := _waiters.get(msg["uid"])) and not future.done():
        future.set_result(reply)
    elif kind == "report" and msg["sender"] == HEAD and HEAD_WAKE:
        task = asyncio.get_running_loop().create_task(_wake(db, domain, msg["thread"]))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def _wake(db: ArcadeDB, domain: str, thread_uid: str) -> None:
    """A teammate finished: the Head reads the thread's unread reports and decides — within the runaway guard."""
    t = await kgdb.get_doc(db, domain, "Thread", thread_uid)
    recent = _wakes[domain]
    while recent and time.monotonic() - recent[0] > 3600:
        recent.popleft()
    if not t or (t.get("wakes") or 0) >= MAX_WAKES or len(recent) >= WAKES_PER_HOUR or domain in _waking:
        return  # the report waits in the inbox
    _waking.add(domain)
    recent.append(time.monotonic())
    try:
        await kgdb.update_doc(db, domain, "Thread", thread_uid, {"wakes": (t.get("wakes") or 0) + 1})
        reports = await kgdb.find_docs(db, domain, "Message", MESSAGE_JSON,
                                       "thread = :t AND recipient = :h AND status = :s", order="created_at ASC",
                                       limit=10, t=thread_uid, h=HEAD, s=UNREAD)
        if not reports:
            return
        history = await _history(db, domain, {"thread": thread_uid, "uid": ""})
        message = (f"[Your team reports — thread {thread_uid}, “{t['title']}”]\n{history}\n\n"
                   f"You were woken because a teammate finished. Decide what follows: store findings worth keeping as "
                   f"facts (ingest_data — only what the sources state), follow up with a teammate on this same thread "
                   f"(assign_task / ask_agent with thread=\"{thread_uid}\"), propose an action, or close it. Don't "
                   f"repeat work that is done. End with a two-sentence note of what you decided.")
        for r in reports:
            await kgdb.update_doc(db, domain, "Message", r["uid"], {"status": READ})
        try:
            note = await _ask_head(domain, message)
        except httpx.HTTPError as e:
            for r in reports:  # the Head is unreachable: back to the inbox
                await kgdb.update_doc(db, domain, "Message", r["uid"], {"status": UNREAD})
            print(f"[Crew] couldn't wake the Head for {domain}: {type(e).__name__}", flush=True)
            return
        await _post(db, domain, thread_uid, HEAD, t["agent"], "note", note.strip() or "(no note)", status=READ)
    finally:
        _waking.discard(domain)
    if (nxt := await _waiting_report(db, domain)) and nxt != thread_uid:
        await _wake(db, domain, nxt)


async def _waiting_report(db: ArcadeDB, domain: str) -> str | None:
    """The oldest thread with an unread report for the Head that may still wake it."""
    for r in await kgdb.find_docs(db, domain, "Message", (), "recipient = :h AND status = :s AND kind = :k",
                                  order="created_at ASC", limit=20, h=HEAD, s=UNREAD, k="report"):
        t = await kgdb.get_doc(db, domain, "Thread", r["thread"])
        if t and t.get("opened_by") == HEAD and (t.get("wakes") or 0) < MAX_WAKES:
            return t["uid"]
    return None


async def _recover(db: ArcadeDB) -> None:
    """After a restart: queued work stays queued; work that was running is reported as interrupted."""
    for domain in registry.list_domains():
        for msg in await kgdb.find_docs(db, domain, "Message", MESSAGE_JSON, "status IN :states", limit=200,
                                        states=[WORKING, WAITING]):
            await _finish(db, domain, msg, Outcome("Interrupted by a restart — assign it again if it is still "
                                                   "needed.", ok=False))


async def work_loop(db: ArcadeDB, idle: float = 30) -> None:
    """The queue worker: one message at a time, oldest first, across every domain."""
    try:
        await _recover(db)
    except Exception as e:
        print(f"[Crew] recovery failed: {type(e).__name__}: {e}", flush=True)
    while True:
        try:
            item = await _next(db)
        except Exception as e:
            print(f"[Crew] queue unreadable: {type(e).__name__}: {e}", flush=True)
            item = None
        if item is None:
            _ready.clear()
            try:
                await asyncio.wait_for(_ready.wait(), idle)
            except asyncio.TimeoutError:
                pass
            continue
        domain, msg = item
        try:
            await _work(db, domain, msg)
        except Exception as e:  # never let one message stop the queue
            print(f"[Crew] {msg['uid']} failed: {type(e).__name__}: {e}", flush=True)
            await kgdb.update_doc(db, domain, "Message", msg["uid"], {"status": FAILED})
