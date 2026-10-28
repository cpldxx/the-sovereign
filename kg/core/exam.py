"""The exam — how much of what the user wants to know the graph can answer, measured the same way every time.

    set    from authoritative sources, never from a model's memory (2026-10-28: questions a local model wrote from
           memory rested on details it got wrong — a standard number, a product name — and could never be answered).
           Per core question of the research charter: a search of the scholarly literature (OpenAlex, PubMed, arXiv)
           and of the web for reviews and official documents, the most authoritative result read in full, and from it a basic, a working and a deep question, each with its
           reference answer and the statement it makes. Round trip: the Validator checks each statement against the
           page, and a question the page doesn't state is dropped (Alberti et al. 2019; premise verification against
           retrieved evidence, EMNLP 2025). Kept until the charter's questions or depth change, new ones are asked
           for, or the user removes one — a new exam (its version) starts a new series
    hold   the exam's sources are held out: research skips them (they count as read), so the graph has to learn the
           answers elsewhere — a test set never trained on
    take   each question goes through Graph RAG (kg.query); the examiner writes the graph's answer from the facts
           found — without seeing the reference — then, apart, grades that answer against the reference: 2 answered,
           1 in part, 0 not, and whether it contradicts it. What the model itself knows earns nothing
    score  100 × points / (2 × questions); per core question the same — open below 25, answered from 75
    loop   the open questions are the learning cycle's first gaps (learning.gaps): research goes where the score is
           lowest — and the next sitting shows whether it worked

Taken after each learning cycle (the research service), once when a charter is confirmed (the baseline), and on demand.
The exam is the Setting document "exam"; each sitting an ExamRun document.
"""

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import date
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior

from agents.validator import validate
from core import activity, auth, charter, kg
from core import database as kgdb
from core.database import ArcadeDB, now
from core.llm import HANGUL, foreign_script, model_for, model_settings

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
MAX_QUESTIONS = 36    # three per core question (basic, working, deep), twelve core questions at most
FACTS_SHOWN = 15      # facts the examiner sees per question (closest first, then the strongest around them)
GRADINGS = 3          # a grade is the middle of three: one grading gave the same answer 1, the next 0 (2026-10-28)
PAGES_TRIED = 2       # pages read per core question until one yields questions
SOURCE_CHARS = 9000   # of a page the examiner reads
MIN_PAGE = 800        # characters a page needs to be worth reading
TRUSTED = 0.5         # the Validator's reliability a kept question needs
ANSWERED, PARTIAL = 75, 25
GROUNDED = 2          # the exam format: 2 = written from sources with reference answers (older exams are set again)
TIERS = ("basic", "working", "deep")
RUN_JSON = ("items", "cores", "graph")
MODEL = model_for("examiner")

_running: set[str] = set()
_tasks: set[asyncio.Task] = set()


class ExamError(ValueError):
    pass


class Busy(RuntimeError):
    """A sitting of the domain is running already."""


# ── What the examiner writes ───────────────────────────────────────────────

class CoreQueries(BaseModel):
    core: int = Field(description="The number of the core question (1-based)")
    queries: list[str] = Field(description="Two web searches for authoritative sources answering it — papers, "
                                           "reviews, official documents: one in English, one in the charter's language")


class Plan(BaseModel):
    cores: list[CoreQueries]


class Pick(BaseModel):
    picks: list[int] = Field(description="The numbers of the most authoritative results, best first (at most 3)")


class Written(BaseModel):
    question: str = Field(description="One question asking one thing, in one short sentence")
    answer: str = Field(description="Its answer as the source states it — short")
    statement: str = Field(description="The question and its answer as one self-contained sentence, as the source "
                                       "states it")


class CoreExam(BaseModel):
    """One question per level — fields, so a model can't write three deep ones (asked in words for a basic, a working
    and a deep question, it wrote twelve compound deep ones: measured 2026-10-28)."""
    basic: Written = Field(description="What anyone learning this must know first")
    working: Written = Field(description="A main finding, number, actor or mechanism")
    deep: Written = Field(description="At the charter's depth: evidence, a debate, or why")


class Grade(BaseModel):
    points: int = Field(ge=0, le=2, description="2: it gives the reference answer; 1: part of it; 0: not")
    conflict: bool = Field(False, description="It states something that contradicts the reference")


class Verdict(BaseModel):
    """A graded question: the graph's answer (written without the reference) and its grade against the reference."""
    points: int = Field(0, ge=0, le=2)
    conflict: bool = False
    answer: str = ""
    facts: list[int] = Field(default_factory=list)


PLAN = """You prepare the exam of the "{domain}" knowledge domain. Its questions will be written from authoritative
sources, so first find them: for each core question of the research charter below (numbered 1-{n} in that order), write
two web searches that would find papers, reviews or official documents answering it — one in English, one in the
charter's language. Plain searches: the core question's own key terms, no invented names or numbers.

The research charter:
{charter}"""

PICK = """Pick the search results most likely to answer this question with authority: peer-reviewed papers and
reviews, official documents (regulators, standards bodies, the companies or institutions themselves), then reputable
press. Skip forums, listicles, ads, SEO pages and anything off the question. Give their numbers, best first."""

WRITE = """You write exam questions FROM THE SOURCE BELOW, for the core question "{core}" of the "{domain}" knowledge
domain (the user wants: {goal}). Three questions, one per level:
- basic: what anyone learning this must know first
- working: a main finding, number, actor or mechanism
- deep: evidence, a debate, or why — as deep as the source goes
Each asks ONE thing in one short sentence, about the subject — never about the source document itself (its title,
authors, venue or date). Everything in a question and its answer must be stated in the source — names, numbers, dates
exactly as written there; nothing from your own memory. Give each its answer as the source states
it, and the question and answer together as one self-contained statement. Write in {language}."""

# Two steps, kept apart: the graph's answer is written without the reference, then compared with it. Shown both at
# once, the examiner wrote the reference into "the graph's answer" (measured 2026-10-28) — answering in isolation keeps
# one from leaking into the other (the factored verification of Chain-of-Verification, Dhuliawala et al. 2023).
# Plain text with [n] citations — what the model writes anyway: asked for a JSON object, it answered in a sentence
# citing "[1][8]" again and again, and a right answer scored nothing (measured 2026-10-28).
ANSWER = """Answer the question from the numbered FACTS only — a knowledge graph's facts; nothing from your own memory.
One or two sentences, citing the facts the answer rests on by their numbers in brackets, like [2][5]. If the facts
don't answer it, say what is missing and cite nothing. Write in the question's language."""

_CITE = re.compile(r"\[(\d+)\]")
_FACTS_LINE = re.compile(r"\n+\s*(?:facts|sources|citations)\s*:.*$", re.I | re.S)


def _cited(text: str, n: int) -> tuple[str, list[int]]:
    """An answer and the facts it cites ([n] in the text, or a trailing "facts: [1, 8]" line) → (clean text, numbers)."""
    numbers = sorted({int(x) for x in _CITE.findall(text) + re.findall(r"\d+", "".join(_FACTS_LINE.findall(text)))
                      if 1 <= int(x) <= n})
    clean = re.sub(r"\s*\[\d+\]", "", _FACTS_LINE.sub("", text)).strip()
    return clean, numbers

COMPARE = """You compare a knowledge graph's ANSWER to a question with the REFERENCE answer from an authoritative source.
- points 2: the answer gives the reference answer — the same key facts, names, numbers (other wording or more detail
  is fine)
- points 1: it gives part of it, or only roughly
- points 0: it doesn't give it — an answer saying the information is missing is 0
- conflict: true when it states something that contradicts the reference
Judge the content, not the language."""


def running(domain: str) -> bool:
    return domain in _running


def _basis(c: dict) -> str:
    """What the exam is drawn from: when it changes, the exam is set again."""
    return hashlib.sha1(json.dumps([c["questions"], c.get("depth")], ensure_ascii=False).encode()).hexdigest()[:12]


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _alike(a: str, b: str) -> bool:
    """Two questions asking the same thing in other words (measured 2026-10-28: one question, three wordings)."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


def _one_language(context: str):
    """An output validator: Korean stays Korean — no Chinese characters or Japanese (core/llm.foreign_script)."""
    def check(out):
        if foreign := foreign_script(context, out if isinstance(out, str) else out.model_dump_json()):
            raise ModelRetry(f"Write it in Korean only — no Chinese characters or Japanese ({foreign}).")
        return out
    return check


async def _ask(output_type, system_prompt: str, prompt: str, context: str = "", decisive: bool = False, check=None):
    """One structured answer, its language checked (`check`: the validator, by default one language). A model that
    fails the check every time is taken as it is: one question's wording must not stop the exam (2026-10-28: answers
    written in English three times over ended a whole sitting)."""
    def agent(validated: bool) -> Agent:
        a = Agent(MODEL, name="examiner", model_settings=model_settings(decisive=decisive), output_type=output_type,
                  retries=2 if validated else 4, system_prompt=system_prompt)
        if validated:
            a.output_validator(check or _one_language(context or prompt))
        return a
    try:
        return (await agent(True).run(prompt)).output
    except UnexpectedModelBehavior as e:
        print(f"[Exam] {getattr(output_type, '__name__', output_type)}: taken unchecked ({e})", flush=True)
        return (await agent(False).run(prompt)).output


# ── The research service: search and read (paced, robots.txt respected) ────

async def _search(query: str) -> list[dict]:
    async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=60, headers=auth.headers()) as client:
        r = await client.get("/search", params={"q": query, "limit": 8})
    r.raise_for_status()
    return r.json().get("results", [])


async def _papers(query: str) -> list[dict]:
    """Papers for a query (OpenAlex, PubMed, arXiv), each with the text a reading of it gives: no page to fetch."""
    async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=90, headers=auth.headers()) as client:
        r = await client.get("/papers", params={"q": query, "limit": 5})
    r.raise_for_status()
    return r.json().get("papers", [])


async def _fetch(url: str) -> dict:
    async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=180, headers=auth.headers()) as client:
        r = await client.post("/fetch", json={"url": url, "max_chars": SOURCE_CHARS})
    r.raise_for_status()
    return r.json()


# ── Setting the exam ───────────────────────────────────────────────────────

async def _doc(db: ArcadeDB, domain: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key="exam")
    return rows[0] if rows else None


async def current(db: ArcadeDB, domain: str) -> dict | None:
    """{version, basis, grounded, created_at, cores, unsourced, questions: [{id, core, tier, question, answer,
    statement, source: {url, title}}]} — or None before the first."""
    doc = await _doc(db, domain) if await db.exists(domain) else None
    return (doc or {}).get("value")


async def held_out(db: ArcadeDB, domain: str) -> set[str]:
    """The exam's source pages: research doesn't learn from them."""
    exam = await current(db, domain)
    return {q["source"]["url"] for q in (exam or {}).get("questions", []) if q.get("source")}


async def _candidates(db: ArcadeDB, domain: str, queries: list[str]) -> list[dict]:
    """Papers for the core question (the first search: the English one), then web results, in the engines' order —
    and the pages the graph already read last (a held-out source should be new to it)."""
    found: dict[str, dict] = {}
    try:
        for p in await _papers(queries[0]):
            venue = " · ".join(str(x) for x in (p.get("venue"), p.get("year")) if x)
            found.setdefault(p["url"], {"title": f"{p['title']} [paper{' · ' + venue if venue else ''}]", "url": p["url"],
                                        "snippet": p.get("abstract", ""), "markdown": p["markdown"]})
    except httpx.HTTPError as e:
        print(f"[Exam] {domain}: paper search failed ({type(e).__name__})", flush=True)
    for q in queries[:3]:
        try:
            for r in await _search(q):
                found.setdefault(r["url"], r)
        except httpx.HTTPError as e:
            print(f"[Exam] {domain}: search failed ({type(e).__name__}): {q[:80]}", flush=True)
    read = set(await kg.known_sources(db, domain, list(found)))
    return [r for u, r in found.items() if u not in read] + [r for u, r in found.items() if u in read]


async def _from_page(domain: str, core: str, goal: str, page: dict) -> list[dict]:
    """A basic, a working and a deep question written from one page — those the page states (round trip)."""
    language = "Korean" if HANGUL.search(core) else "the core question's language"
    text = page["markdown"][:SOURCE_CHARS]
    with activity.working(domain, "learning", f"exam: writing from {urlsplit(page['url']).netloc}"):
        written = await _ask(CoreExam, WRITE.format(core=core, domain=domain, goal=goal, language=language),
                             f"SOURCE ({page.get('title') or page['url']}):\n{text}", context=core)
    tiers = [(t, getattr(written, t)) for t in TIERS]
    tiers = [(t, w) for t, w in tiers if w.question.strip() and w.statement.strip()]
    with activity.working(domain, "validator", "exam: checking questions against their source"):
        verdicts = await validate(text, page["url"], [w.statement for _, w in tiers])
    return [{"tier": t, "question": " ".join(w.question.split()), "answer": w.answer.strip(),
             "statement": w.statement.strip(), "source": {"url": page["url"], "title": page.get("title") or ""}}
            for (t, w), v in zip(tiers, verdicts) if v.supported and v.reliability >= TRUSTED]


async def _set(db: ArcadeDB, domain: str, c: dict) -> tuple[list[dict], list[int]]:
    """(questions, the core questions no source was found for)."""
    n = len(c["questions"])
    with activity.working(domain, "learning", "exam: planning the searches"):
        plan = await _ask(Plan, PLAN.format(domain=domain, n=n, charter=charter.text(c)), "Plan the searches.",
                          context=charter.text(c))
    queries = {x.core: [q for q in x.queries if q.strip()] for x in plan.cores if 1 <= x.core <= n}
    questions: list[dict] = []
    unsourced: list[int] = []
    for i, core in enumerate(c["questions"], start=1):
        with activity.working(domain, "research", f"exam sources {i}/{n}"):
            results = await _candidates(db, domain, queries.get(i) or [core])
        mine: list[dict] = []
        try:
            mine = await _core_questions(domain, core, c["goal"], results, questions)
        except Exception as e:   # one core question's trouble leaves it unsourced, not the exam unset
            print(f"[Exam] {domain}: core question {i} failed: {type(e).__name__}: {e}"[:300], flush=True)
        if not mine:
            unsourced.append(i)
        questions += [{"core": i, **q} for q in mine]
    return [{"id": f"q{k + 1}", **q} for k, q in enumerate(questions[:MAX_QUESTIONS])], unsourced


async def _core_questions(domain: str, core: str, goal: str, results: list[dict], questions: list[dict]) -> list[dict]:
    """From the most authoritative of the results: the questions one page states (none if no page could be read)."""
    mine: list[dict] = []
    if results:
        listing = "\n".join(f"[{k + 1}] {r['title']} — {r['url']}\n    {r.get('snippet', '')[:200]}"
                            for k, r in enumerate(results[:12]))
        pick = await _ask(Pick, PICK, f"QUESTION: {core}\n\nRESULTS:\n{listing}", decisive=True)
        order = [results[k - 1] for k in dict.fromkeys(pick.picks) if 1 <= k <= min(len(results), 12)]
        tried = 0
        for r in order[:3]:
            if tried >= PAGES_TRIED or mine:
                break
            if r.get("markdown"):    # a paper: its abstract is the source, nothing to fetch
                page = {"url": r["url"], "title": r["title"], "status": "full", "markdown": r["markdown"]}
            else:
                try:
                    with activity.working(domain, "research", f"exam: reading {urlsplit(r['url']).netloc}"):
                        page = await _fetch(r["url"])
                except httpx.HTTPError:
                    continue
            if page.get("status") == "failed" or len(page.get("markdown") or "") < MIN_PAGE:
                continue
            tried += 1
            mine = [q for q in await _from_page(domain, core, goal, page)
                    if not any(_alike(q["question"], x["question"]) for x in questions)]
    return mine


async def _save(db: ArcadeDB, domain: str, value: dict) -> dict:
    doc = await _doc(db, domain)
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "exam", "value": value}, ("value",))
    return value


async def ensure(db: ArcadeDB, domain: str, fresh: bool = False) -> dict:
    """The exam for the charter as it is now: the current one, or a new version when the charter's questions or
    depth changed, the exam predates sources (GROUNDED) — or `fresh` (new questions from the same charter: the score
    starts a new series). ExamError without a charter, or when no source could be found for any core question."""
    c = (await charter.get(db, domain))["charter"]
    if not c:
        raise ExamError("The exam is drawn from the research charter — there is none yet")
    exam = await current(db, domain)
    if exam and exam.get("basis") == _basis(c) and exam.get("grounded") == GROUNDED and not fresh:
        return exam
    questions, unsourced = await _set(db, domain, c)
    if not questions:
        raise ExamError("No authoritative source could be read for any core question — is the research service up?")
    return await _save(db, domain, {"version": (exam or {}).get("version", 0) + 1, "basis": _basis(c),
                                    "grounded": GROUNDED, "created_at": now(), "cores": list(c["questions"]),
                                    "unsourced": unsourced, "questions": questions})


async def remove(db: ArcadeDB, domain: str, qid: str) -> dict:
    """The user drops a question (off the mark): a new version without it — the score starts a new series."""
    exam = await current(db, domain)
    if not exam or not any(q["id"] == qid for q in exam["questions"]):
        raise ExamError(f"No question {qid} in the current exam")
    rest = [q for q in exam["questions"] if q["id"] != qid]
    if not rest:
        raise ExamError("That is the exam's last question — set new questions instead")
    return await _save(db, domain, {**exam, "version": exam["version"] + 1, "created_at": now(), "questions": rest})


# ── Taking it ──────────────────────────────────────────────────────────────

async def _grade(domain: str, q: dict, facts: list[dict]) -> Verdict:
    """The graph's answer from its facts alone, then its grade against the reference."""
    korean = HANGUL.search(q["question"])
    if not facts:
        return Verdict(answer="그래프에 아직 이 내용이 없어요." if korean else "The graph holds nothing on this yet.")
    shown = "\n".join(f"[{i + 1}] {f['fact']}" for i, f in enumerate(facts))

    def checked(a: str) -> str:
        # Asked "in the question's language", it answered Korean questions in English (measured 2026-10-28).
        if korean and not HANGUL.search(a):
            raise ModelRetry("The question is in Korean: write the answer in Korean.")
        if foreign := foreign_script(q["question"], a):
            raise ModelRetry(f"Write the answer in Korean only — no Chinese characters or Japanese ({foreign}).")
        return a

    # The facts are often English: said last, the answer's language holds (said once in the system prompt, it didn't).
    language = "\n\nWrite the answer in Korean (한국어로)." if korean else ""
    text = await _ask(str, ANSWER, f"QUESTION: {q['question']}\n\nFACTS:\n{shown}{language}", decisive=True,
                      check=checked)
    answer, cited = _cited(str(text), len(facts))
    v = Verdict(answer=answer, facts=cited)
    if not cited or not q.get("answer"):
        return v                                      # an answer resting on no fact knows nothing
    prompt = f"QUESTION: {q['question']}\nREFERENCE: {q['answer']}\nANSWER: {v.answer}"
    grades = [await _ask(Grade, COMPARE, prompt, decisive=True) for _ in range(GRADINGS)]
    v.points = sorted(g.points for g in grades)[GRADINGS // 2]
    v.conflict = sum(g.conflict for g in grades) > GRADINGS // 2
    return v


def _status(score: int | None) -> str:
    if score is None:
        return "unset"
    return "answered" if score >= ANSWERED else "partial" if score >= PARTIAL else "open"


async def take(db: ArcadeDB, domain: str, fresh: bool = False) -> dict:
    """One sitting: every question answered from the graph alone, graded against its reference, stored. `fresh`: new
    questions first. ExamError without a charter; Busy while another sitting of the domain runs."""
    if domain in _running:
        raise Busy(f"The exam of {domain} is being taken already")
    _running.add(domain)
    started = time.monotonic()
    try:
        exam = await ensure(db, domain, fresh)
        items = []
        for i, q in enumerate(exam["questions"]):
            found: list[dict] = []
            try:
                with activity.working(domain, "learning", f"exam {i + 1}/{len(exam['questions'])}"):
                    found = (await kg.query(db, domain, q["question"], 8))["facts"][:FACTS_SHOWN]
                    v = await _grade(domain, q, found)
            except Exception as e:   # one question that can't be graded scores nothing; the sitting goes on
                print(f"[Exam] {domain}: {q['id']} not graded: {type(e).__name__}: {e}"[:300], flush=True)
                v = Verdict(answer=("채점하지 못했어요" if HANGUL.search(q["question"]) else "Not graded")
                                   + f" ({type(e).__name__})")
            cited = [found[n - 1] for n in v.facts]
            items.append({"id": q["id"], "core": q["core"], "tier": q.get("tier"), "question": q["question"],
                          "reference": q.get("answer", ""), "source": q.get("source"), "points": v.points,
                          "conflict": v.conflict, "answer": v.answer.strip(), "facts": [f["uid"] for f in cited],
                          "sources": len({s for f in cited for s in f.get("sources") or []}),
                          "well_sourced": any((f.get("evidence") or 0) >= 2 for f in cited)})
        cores = []
        for n, question in enumerate(exam["cores"], start=1):
            mine = [x["points"] for x in items if x["core"] == n]
            score = round(100 * sum(mine) / (2 * len(mine))) if mine else None
            cores.append({"core": n, "question": question, "score": score, "status": _status(score)})
        stats = await kgdb.domain_stats(db, domain)
        run = {"version": exam["version"], "score": round(100 * sum(x["points"] for x in items) / (2 * len(items))),
               "answered": sum(x["points"] == 2 for x in items), "partial": sum(x["points"] == 1 for x in items),
               "open": sum(x["points"] == 0 for x in items), "conflicts": sum(x["conflict"] for x in items),
               "total": len(items), "items": items, "cores": cores,
               "graph": {"entities": stats["entity_count"], "facts": stats["fact_count"]},
               "seconds": round(time.monotonic() - started), "n": time.time_ns()}   # n orders sittings
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
