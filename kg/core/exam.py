"""The exam — how much of what the user wants to know the graph can answer, measured the same way every time.

    set    from the research charter, never from the graph (questions drawn from what the graph already holds would
           pass by construction): per core question a basic, a working and a deep question — the score climbs as the
           graph goes deeper — kept until the charter's questions or depth change (or new ones are asked for); a new
           exam (its version) starts a new series
    take   each question goes through Graph RAG (kg.query); the examiner sees only the facts found and judges whether
           they answer it — 2 answered, 1 in part, 0 not — writing the answer from those facts alone and citing them.
           What the model itself knows earns nothing: the score measures the graph
    score  100 × points / (2 × questions); per core question the same — open below 25, answered from 75
    loop   the open questions are the learning cycle's first gaps (learning.gaps): research goes where the score is
           lowest — and the next sitting shows whether it worked

Taken after each learning cycle (the research service), once when a charter is confirmed (the baseline), and on demand.
The exam is the Setting document "exam"; each sitting an ExamRun document.
"""

import asyncio
import hashlib
import json
import re
import time
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from core import activity, charter, kg
from core import database as kgdb
from core.database import ArcadeDB, now
from core.llm import HANGUL, foreign_script, model_for, model_settings

MAX_QUESTIONS = 36    # three per core question (basic, working, deep), twelve core questions at most
FACTS_SHOWN = 15      # facts the examiner sees per question (closest first, then the strongest around them)
ANSWERED, PARTIAL = 75, 25
RUN_JSON = ("items", "cores", "graph")
MODEL = model_for("examiner")

_running: set[str] = set()
_tasks: set[asyncio.Task] = set()


class ExamError(ValueError):
    pass


class CoreExam(BaseModel):
    """Three questions for one core question, one per level — fields, so a model can't write three deep ones (asked in
    words for a basic, a working and a deep question, it wrote twelve compound deep ones: measured 2026-10-28)."""
    core: int = Field(description="The number of the core question (1-based)")
    basic: str = Field(description="What anyone learning this must know first — one simple question")
    working: str = Field(description="A main finding, number, actor or mechanism — one question")
    deep: str = Field(description="At the charter's depth: evidence, a debate, or why — one question")


class ExamSet(BaseModel):
    cores: list[CoreExam]


TIERS = ("basic", "working", "deep")


class Verdict(BaseModel):
    points: Literal[0, 1, 2] = Field(description="2: the facts answer it; 1: they answer part of it; 0: they don't")
    answer: str = Field(description="The answer from the numbered facts only — or what is missing")
    facts: list[int] = Field(default_factory=list, description="The numbers of the facts the answer rests on")


SET = """You write the exam of the "{domain}" knowledge domain: questions that test whether its knowledge graph knows
what its user wants to know. Today is {today}.

The research charter:
{charter}

For each core question (numbered 1-{n} in the order above) write three questions, one per level:
- basic: what anyone learning this must know first (e.g. "What is lecanemab?")
- working: a main finding, number, actor or mechanism (e.g. "What did the Clarity AD trial find?")
- deep: at the charter's depth — evidence, a debate, or why (e.g. "Why do experts disagree about lecanemab's benefit?")
Each asks ONE thing in one short sentence and is answerable from public sources. Use only names and terms the charter
uses or that you are certain exist: never invent a standard's number, an acronym's expansion or a product detail — a
question with a wrong premise can never be answered. Avoid what the charter leaves out. Write them in the charter's
language."""

JUDGE = """You grade one exam question against what a knowledge graph holds. You see ONLY the numbered facts below,
retrieved from the graph for this question. Judge strictly from them — what you know yourself earns nothing:
- points 2: the facts answer the question (its core, not a neighbouring topic)
- points 1: they answer part of it, or only roughly
- points 0: they don't answer it
Then write the answer in one or two sentences using only those facts (or say what is missing), and list the numbers
of the facts it rests on. Write in the question's language."""


def running(domain: str) -> bool:
    return domain in _running


def _basis(c: dict) -> str:
    """What the exam is drawn from: when it changes, the exam is set again."""
    return hashlib.sha1(json.dumps([c["questions"], c.get("depth")], ensure_ascii=False).encode()).hexdigest()[:12]


# ── The exam ───────────────────────────────────────────────────────────────

async def _doc(db: ArcadeDB, domain: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key="exam")
    return rows[0] if rows else None


async def current(db: ArcadeDB, domain: str) -> dict | None:
    """{version, basis, created_at, cores, questions: [{id, core, tier, question}]} — or None before the first."""
    doc = await _doc(db, domain) if await db.exists(domain) else None
    return (doc or {}).get("value")


async def _set(domain: str, c: dict) -> list[dict]:
    agent = Agent(MODEL, name="examiner", model_settings=model_settings(), output_type=ExamSet, retries=2,
                  system_prompt=SET.format(domain=domain, today=date.today().isoformat(), charter=charter.text(c),
                                           n=len(c["questions"])))

    @agent.output_validator
    def checked(out: ExamSet) -> ExamSet:
        if foreign := foreign_script(charter.text(c), " ".join(getattr(x, t) for x in out.cores for t in TIERS)):
            raise ModelRetry(f"Write the questions in Korean only — no Chinese characters or Japanese ({foreign}).")
        return out

    out = (await agent.run("Write the exam.")).output
    questions: list[dict] = []
    for x in out.cores:
        if not 1 <= x.core <= len(c["questions"]) or any(q["core"] == x.core for q in questions):
            continue   # one set per core question
        for tier in TIERS:
            text = " ".join(getattr(x, tier).split())
            if text and not any(_alike(text, q["question"]) for q in questions):
                questions.append({"core": x.core, "tier": tier, "question": text})
    questions.sort(key=lambda q: q["core"])   # the charter's order
    if not questions:
        raise ExamError("The examiner wrote no usable question — try again")
    return [{"id": f"q{i + 1}", **q} for i, q in enumerate(questions[:MAX_QUESTIONS])]


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _alike(a: str, b: str) -> bool:
    """Two questions asking the same thing in other words (measured 2026-10-28: one question, three wordings)."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


async def ensure(db: ArcadeDB, domain: str, fresh: bool = False) -> dict:
    """The exam for the charter as it is now: the current one, or a new version when the charter's questions or
    depth changed — or `fresh` (new questions from the same charter: the score starts a new series). ExamError
    without a charter."""
    c = (await charter.get(db, domain))["charter"]
    if not c:
        raise ExamError("The exam is drawn from the research charter — there is none yet")
    exam = await current(db, domain)
    if exam and exam.get("basis") == _basis(c) and not fresh:
        return exam
    value = {"version": (exam or {}).get("version", 0) + 1, "basis": _basis(c), "created_at": now(),
             "cores": list(c["questions"]), "questions": await _set(domain, c)}
    doc = await _doc(db, domain)
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "exam", "value": value}, ("value",))
    return value


# ── Taking it ──────────────────────────────────────────────────────────────

async def _grade(domain: str, question: str, facts: list[dict]) -> Verdict:
    if not facts:
        return Verdict(points=0, answer="The graph holds nothing on this yet.")
    agent = Agent(MODEL, name="examiner", model_settings=model_settings(decisive=True), output_type=Verdict,
                  retries=2, system_prompt=JUDGE)

    @agent.output_validator
    def checked(v: Verdict) -> Verdict:
        # Asked "in the question's language", it answered Korean questions in English (measured 2026-10-28).
        if HANGUL.search(question) and not HANGUL.search(v.answer):
            raise ModelRetry("The question is in Korean: write the answer in Korean.")
        if foreign := foreign_script(question, v.answer):
            raise ModelRetry(f"Write the answer in Korean only — no Chinese characters or Japanese ({foreign}).")
        return v

    shown = "\n".join(f"[{i + 1}] {f['fact']}" for i, f in enumerate(facts))
    v = (await agent.run(f"QUESTION: {question}\n\nFACTS:\n{shown}")).output
    v.facts = sorted({n for n in v.facts if 1 <= n <= len(facts)})
    if v.points and not v.facts:
        v.points = 1 if v.points == 2 else v.points   # an answer resting on no fact is at most partial
    return v


def _status(score: int) -> str:
    return "answered" if score >= ANSWERED else "partial" if score >= PARTIAL else "open"


async def take(db: ArcadeDB, domain: str, fresh: bool = False) -> dict:
    """One sitting: every question answered from the graph alone, graded, stored. `fresh`: new questions first.
    ExamError without a charter; RuntimeError while another sitting of the domain runs."""
    if domain in _running:
        raise RuntimeError(f"The exam of {domain} is being taken already")
    _running.add(domain)
    started = time.monotonic()
    try:
        exam = await ensure(db, domain, fresh)
        items = []
        for i, q in enumerate(exam["questions"]):
            with activity.working(domain, "learning", f"exam {i + 1}/{len(exam['questions'])}"):
                found = (await kg.query(db, domain, q["question"], 8))["facts"][:FACTS_SHOWN]
                v = await _grade(domain, q["question"], found)
            cited = [found[n - 1] for n in v.facts]
            items.append({"id": q["id"], "core": q["core"], "tier": q.get("tier"), "question": q["question"], "points": v.points,
                          "answer": v.answer.strip(), "facts": [f["uid"] for f in cited],
                          "sources": len({s for f in cited for s in f.get("sources") or []}),
                          "well_sourced": any((f.get("evidence") or 0) >= 2 for f in cited)})
        cores = []
        for n, question in enumerate(exam["cores"], start=1):
            mine = [x["points"] for x in items if x["core"] == n]
            score = round(100 * sum(mine) / (2 * len(mine))) if mine else 0
            cores.append({"core": n, "question": question, "score": score, "status": _status(score)})
        stats = await kgdb.domain_stats(db, domain)
        run = {"version": exam["version"], "score": round(100 * sum(x["points"] for x in items) / (2 * len(items))),
               "answered": sum(x["points"] == 2 for x in items), "partial": sum(x["points"] == 1 for x in items),
               "open": sum(x["points"] == 0 for x in items), "total": len(items), "items": items, "cores": cores,
               "graph": {"entities": stats["entity_count"], "facts": stats["fact_count"]},
               "seconds": round(time.monotonic() - started), "n": time.time_ns()}   # n orders sittings (created_at: seconds)
        return await kgdb.insert_doc(db, domain, "ExamRun", run, RUN_JSON)
    finally:
        _running.discard(domain)


def start(db: ArcadeDB, domain: str, fresh: bool = False) -> bool:
    """A sitting in the background (e.g. the baseline when a charter is confirmed). False if one is running."""
    if domain in _running:
        return False

    async def sit() -> None:
        try:
            run = await take(db, domain, fresh)
            print(f"[Exam] {domain}: {run['score']}/100 ({run['answered']} answered, {run['open']} open)", flush=True)
        except Exception as e:
            print(f"[Exam] {domain} failed: {type(e).__name__}: {e}", flush=True)

    task = asyncio.get_running_loop().create_task(sit())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


# ── Reading the results ────────────────────────────────────────────────────

async def history(db: ArcadeDB, domain: str, limit: int = 60) -> list[dict]:
    """Sittings, oldest first, without their answers: {uid, created_at, version, score, answered, partial, open,
    total, graph}."""
    rows = await kgdb.find_docs(db, domain, "ExamRun", ("graph",), order="n DESC", limit=limit)
    keep = ("uid", "created_at", "version", "score", "answered", "partial", "open", "total", "graph")
    return [{k: r.get(k) for k in keep} for r in reversed(rows)]


async def latest(db: ArcadeDB, domain: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "ExamRun", RUN_JSON, order="n DESC", limit=1)
    return rows[0] if rows else None


async def open_questions(db: ArcadeDB, domain: str, n: int = 8) -> list[str]:
    """The questions of the current exam the graph could not answer at its latest sitting (then the half-answered):
    where research should go first."""
    run, exam = await latest(db, domain), await current(db, domain)
    if not run or not exam or run["version"] != exam["version"]:
        return []
    ranked = sorted((x for x in run["items"] if x["points"] < 2), key=lambda x: x["points"])
    return [x["question"] for x in ranked[:n]]


async def line(db: ArcadeDB, domain: str) -> str:
    """The exam for a briefing: the latest score and the one before it, in the same exam."""
    runs = await history(db, domain, 2)
    if not runs:
        return ""
    last = runs[-1]
    before = runs[-2] if len(runs) > 1 and runs[-2]["version"] == last["version"] else None
    change = f" (was {before['score']})" if before else ""
    return (f"EXAM (the research charter's questions, answered from the graph alone): {last['score']}/100{change} — "
            f"{last['answered']} of {last['total']} answered, {last['open']} open")
