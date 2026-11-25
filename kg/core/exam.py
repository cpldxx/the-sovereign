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
           answers elsewhere — a test set never trained on. And they are new to the graph when set: no page it has
           read, nor a copy of one under another address (core/near.py)
    two    sets, set the same way from different sources: the PRACTICE questions say where research goes (their open
           ones are the learning cycle's first gaps) and are what a change to the system is tried on; the EXAM only
           measures. A score that steers what is learned, or what is changed, stops measuring (2026-10-29: four
           missions on the exam's open questions, then retrieval fixes found by reading its answers — the exam had
           become the thing taught to). Both sets' sources are held out
    take   each question goes through Graph RAG (kg.query); the examiner writes the graph's answer from the facts
           found — without seeing the reference — then, apart, grades that answer against the reference: 2 answered,
           1 in part, 0 not, and whether it contradicts it. What the model itself knows earns nothing. At temperature
           0, a question shown the same facts as at the last sitting keeps its verdict, and an answer written as
           before keeps its grade: the score moves only when the graph's answers do (2026-10-28: one graph scored 41,
           23 and 36 — and an answer the same to the byte, graded 1 three times, got 0 while the GPU was shared)
    score  100 × points / (2 × questions); per core question the same — open below 25, answered from 75
    loop   the practice set's open questions are the learning cycle's first gaps (learning.gaps): research goes where
           practice is weakest — and the exam's next sitting shows whether the graph learned the subject

Taken after each learning cycle (the research service), once when a charter is confirmed (the baseline), and on demand.
The exam is the Setting document "exam", the practice set "exam_practice"; each sitting an ExamRun document (its
`kind`: "practice", or none for the exam).
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
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from agents.validator import validate
from core import activity, auth, charter, kg, near, sources
from core import database as kgdb
from core.database import ArcadeDB, now
from core.llm import model_for, model_settings

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
MAX_QUESTIONS = 36    # three per core question and page (basic, working, deep), twelve core questions at most
FACTS_SHOWN = 15      # facts the examiner sees per question (closest first, then the strongest around them)
PAGES_TRIED = 2       # pages read per core question until one yields questions
# Pages a core question's questions come from. The exam had 11 questions from one page each (2026-10-30): one question
# moved the score by 4.5, and 41 → 55 was three questions — three pages make a point of it about 1.5.
PAGES = {"exam": 3, "practice": 2}
SOURCE_CHARS = 9000   # of a page the examiner reads
MIN_PAGE = 800        # characters a page needs to be worth reading
TRUSTED = 0.5         # the Validator's reliability a kept question needs
ANSWERED, PARTIAL = 75, 25
GROUNDED = 2          # the exam format: 2 = written from sources with reference answers (older exams are set again)
TIERS = ("basic", "working", "deep")
KINDS = {"exam": "exam", "practice": "exam_practice"}   # set → its Setting key
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
    picks: list[int] = Field(description="The numbers of the most authoritative results, best first (at most 6)")


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
press — each is marked with its kind. Skip forums, listicles, ads, SEO pages and anything off the question. Give their
numbers, best first."""

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
# Who grades: another prompt or model grades every answer anew.
GRADER = hashlib.sha1(f"{COMPARE}|{getattr(MODEL, 'model_name', MODEL)}".encode()).hexdigest()[:8]


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


async def _ask(output_type, system_prompt: str, prompt: str, decisive: bool = False, exact: bool = False):
    """One structured answer, asked once more with more retries when the model's output doesn't fit: one answer the
    model can't shape must not stop the exam. `exact`: temperature 0 — the same graph gets the same answer and grade
    (at 0.1 one graph scored 41, 23 and 36: answers took either side of a debate, one answer was graded 1, 0 and 1 —
    2026-10-28)."""
    def agent(retries: int) -> Agent:
        settings = {**model_settings("examiner", decisive=decisive), **({"temperature": 0.0} if exact else {})}
        return Agent(MODEL, name="examiner", model_settings=settings, output_type=output_type, retries=retries,
                     system_prompt=system_prompt)
    try:
        return (await agent(2).run(prompt)).output
    except UnexpectedModelBehavior as e:
        print(f"[Exam] {getattr(output_type, '__name__', output_type)}: asked again ({e})", flush=True)
        return (await agent(4).run(prompt)).output


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

async def _doc(db: ArcadeDB, domain: str, kind: str = "exam") -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key=KINDS[kind])
    return rows[0] if rows else None


async def current(db: ArcadeDB, domain: str, kind: str = "exam") -> dict | None:
    """{version, basis, grounded, created_at, cores, unsourced, questions: [{id, core, tier, question, answer,
    statement, source: {url, title}}]} — or None before the first."""
    doc = await _doc(db, domain, kind) if await db.exists(domain) else None
    return (doc or {}).get("value")


def _other(kind: str) -> str:
    return "practice" if kind == "exam" else "exam"


async def held_out(db: ArcadeDB, domain: str) -> set[str]:
    """The source pages of the exam and of the practice set: research doesn't learn from them."""
    return {q["source"]["url"] for kind in KINDS for q in ((await current(db, domain, kind)) or {}).get("questions", [])
            if q.get("source")}


async def _candidates(db: ArcadeDB, domain: str, queries: list[str], taken: set[str] = frozenset()) -> list[dict]:
    """Papers for the core question (the first search: the English one), then web results — by tier (official before
    press before other sites, the engines' order within one), blogs and forums left out: a reference must have authority
    (2026-10-28: a company blog's "HBM4 moves to hybrid bonding" stood against papers saying it keeps microbumps). Left
    out too: the pages the graph has read — a question from one tests what the graph copied, not what it learned
    (until 2026-11-25 they only went last) — and those the other set is drawn from (`taken`): practice must not
    rehearse the exam's pages. Copies of read pages under other addresses are left out once read (_core_questions)."""
    found: dict[str, dict] = {}
    try:
        for p in await _papers(queries[0]):
            venue = " · ".join(str(x) for x in (p.get("venue"), p.get("year")) if x)
            found.setdefault(p["url"], {"title": f"{p['title']} [paper{' · ' + venue if venue else ''}]", "url": p["url"],
                                        "snippet": p.get("abstract", ""), "markdown": p["markdown"], "tier": "paper"})
    except httpx.HTTPError as e:
        print(f"[Exam] {domain}: paper search failed ({type(e).__name__})", flush=True)
    for q in queries[:3]:
        try:
            for r in await _search(q):
                found.setdefault(r["url"], {**r, "tier": sources.tier_of(r["url"])})
        except httpx.HTTPError as e:
            print(f"[Exam] {domain}: search failed ({type(e).__name__}): {q[:80]}", flush=True)
    ranked = sorted((r for r in found.values() if r["tier"] != "blog" and r["url"] not in taken),
                    key=lambda r: sources.TIERS.index(r["tier"]))
    read = set(await kg.known_sources(db, domain, [r["url"] for r in ranked]))
    return [r for r in ranked if r["url"] not in read]


async def _from_page(domain: str, core: str, goal: str, page: dict) -> list[dict]:
    """A basic, a working and a deep question written from one page — those the page states (round trip)."""
    language = "the core question's language"
    text = page["markdown"][:SOURCE_CHARS]
    with activity.working(domain, "learning", f"exam: writing from {urlsplit(page['url']).netloc}"):
        written = await _ask(CoreExam, WRITE.format(core=core, domain=domain, goal=goal, language=language),
                             f"SOURCE ({page.get('title') or page['url']}):\n{text}")
    tiers = [(t, getattr(written, t)) for t in TIERS]
    tiers = [(t, w) for t, w in tiers if w.question.strip() and w.statement.strip()]
    with activity.working(domain, "validator", "exam: checking questions against their source"):
        verdicts = await validate(text, page["url"], [w.statement for _, w in tiers])
    return [{"tier": t, "question": " ".join(w.question.split()), "answer": w.answer.strip(),
             "statement": w.statement.strip(), "source": {"url": page["url"], "title": page.get("title") or ""}}
            for (t, w), v in zip(tiers, verdicts) if v.supported and v.reliability >= TRUSTED]


async def _set(db: ArcadeDB, domain: str, c: dict, other: dict | None = None,
               pages: int = 1) -> tuple[list[dict], list[int]]:
    """(questions, the core questions no source was found for) — none from the sources of the `other` set, or like
    its questions."""
    taken = {q["source"]["url"] for q in (other or {}).get("questions", []) if q.get("source")}
    read = await kg.read_pages(db, domain)
    n = len(c["questions"])
    with activity.working(domain, "learning", "exam: planning the searches"):
        plan = await _ask(Plan, PLAN.format(domain=domain, n=n, charter=charter.text(c)), "Plan the searches.")
    queries = {x.core: [q for q in x.queries if q.strip()] for x in plan.cores if 1 <= x.core <= n}
    questions: list[dict] = []
    unsourced: list[int] = []
    for i, core in enumerate(c["questions"], start=1):
        with activity.working(domain, "research", f"exam sources {i}/{n}"):
            results = await _candidates(db, domain, queries.get(i) or [core], taken)
        mine: list[dict] = []
        try:
            mine = await _core_questions(domain, core, c["goal"], results, questions + (other or {}).get("questions", []),
                                         pages, read)
        except Exception as e:   # one core question's trouble leaves it unsourced, not the exam unset
            print(f"[Exam] {domain}: core question {i} failed: {type(e).__name__}: {e}"[:300], flush=True)
        if not mine:
            unsourced.append(i)
        questions += [{"core": i, **q} for q in mine]
    return [{"id": f"q{k + 1}", **q} for k, q in enumerate(questions[:MAX_QUESTIONS * pages])], unsourced


async def _core_questions(domain: str, core: str, goal: str, results: list[dict], questions: list[dict],
                          pages: int = 1, read: dict[str, dict] | None = None) -> list[dict]:
    """From the most authoritative of the results: the questions `pages` pages state (none if no page could be read).
    A 4-question practice set came of one page per core question (2026-10-29): the exam had taken the best ones.
    A page that is a copy of one the graph has read (`read`: kg.read_pages) is left out and doesn't use up a try."""
    mine: list[dict] = []
    if results:
        listing = "\n".join(f"[{k + 1}] {r['title']}{'' if r['tier'] == 'paper' else ' [' + r['tier'] + ']'} — {r['url']}"
                            f"\n    {r.get('snippet', '')[:200]}" for k, r in enumerate(results[:12]))
        pick = await _ask(Pick, PICK, f"QUESTION: {core}\n\nRESULTS:\n{listing}", decisive=True)
        order = [results[k - 1] for k in dict.fromkeys(pick.picks) if 1 <= k <= min(len(results), 12)]
        tried = kept = 0
        left = pages + PAGES_TRIED   # pages looked at, besides copies of pages the graph has read
        for r in order:
            if left <= 0 or tried >= PAGES_TRIED + pages - 1 or kept >= pages:
                break
            left -= 1
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
            seen = near.copy_in(near.sketch(kg.readable(page["markdown"][:SOURCE_CHARS])), read or {})
            if seen:
                left += 1
                print(f"[Exam] {domain}: {page['url']} left out — a copy of a page the graph has read ({seen})",
                      flush=True)
                continue
            tried += 1
            new = [q for q in await _from_page(domain, core, goal, page)
                   if not any(_alike(q["question"], x["question"]) for x in questions + mine)]
            kept += bool(new)
            mine += new
    return mine


async def _save(db: ArcadeDB, domain: str, value: dict, kind: str = "exam") -> dict:
    doc = await _doc(db, domain, kind)
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": KINDS[kind], "value": value}, ("value",))
    return value


async def ensure(db: ArcadeDB, domain: str, fresh: bool = False, kind: str = "exam") -> dict:
    """The exam (or the practice set) for the charter as it is now: the current one, or a new version when the
    charter's questions or depth changed, it predates sources (GROUNDED) — or `fresh` (new questions from the same
    charter: the score starts a new series). Never from the other set's sources. ExamError without a charter, or when
    no source could be found for any core question."""
    c = (await charter.get(db, domain))["charter"]
    if not c:
        raise ExamError("The exam is drawn from the research charter — there is none yet")
    exam = await current(db, domain, kind)
    if exam and exam.get("basis") == _basis(c) and exam.get("grounded") == GROUNDED and not fresh:
        return exam
    questions, unsourced = await _set(db, domain, c, await current(db, domain, _other(kind)), PAGES[kind])
    if not questions:
        raise ExamError("No authoritative source could be read for any core question — is the research service up?")
    return await _save(db, domain, {"version": (exam or {}).get("version", 0) + 1, "basis": _basis(c),
                                    "grounded": GROUNDED, "created_at": now(), "cores": list(c["questions"]),
                                    "unsourced": unsourced, "questions": questions}, kind)


async def remove(db: ArcadeDB, domain: str, qid: str, kind: str = "exam") -> dict:
    """The user drops a question (off the mark): a new version without it — the score starts a new series."""
    exam = await current(db, domain, kind)
    if not exam or not any(q["id"] == qid for q in exam["questions"]):
        raise ExamError(f"No question {qid} in the current {kind}")
    rest = [q for q in exam["questions"] if q["id"] != qid]
    if not rest:
        raise ExamError(f"That is the {kind}'s last question — set new questions instead")
    return await _save(db, domain, {**exam, "version": exam["version"] + 1, "created_at": now(), "questions": rest},
                       kind)


# ── Taking it ──────────────────────────────────────────────────────────────

def _graded(question: str, reference: str, answer: str) -> str:
    return hashlib.sha1(json.dumps([question, reference, answer], ensure_ascii=False).encode()).hexdigest()[:16]


async def _grades(db: ArcadeDB, domain: str, kind: str, runs: int = 20) -> dict[str, tuple[int, bool]]:
    """The grades this grader gave at the recent sittings, by question, reference and answer: the same answer is
    graded the same. 2026-10-29: practice sat twice on one graph wrote all 19 answers the same to the byte, and two
    got 1 one time, 0 the next (the GPU shared) — the score moved by 5 with nothing changed."""
    memo: dict[str, tuple[int, bool]] = {}
    for run in reversed(await kgdb.find_docs(db, domain, "ExamRun", RUN_JSON, _of(kind), order="n DESC", limit=runs)):
        for x in run.get("items") or []:
            if x.get("grader") == GRADER and x.get("facts"):
                memo[_graded(x["question"], x.get("reference", ""), x["answer"])] = (x["points"], x["conflict"])
    return memo


async def _grade(domain: str, q: dict, facts: list[dict], memo: dict | None = None) -> Verdict:
    """The graph's answer from its facts alone, then its grade against the reference — the one it had before (`memo`)
    if the answer is the same."""
    if not facts:
        return Verdict(answer="The graph holds nothing on this yet.")
    shown = "\n".join(f"[{i + 1}] {f['fact']}" for i, f in enumerate(facts))
    text = await _ask(str, ANSWER, f"QUESTION: {q['question']}\n\nFACTS:\n{shown}", exact=True)
    answer, cited = _cited(str(text), len(facts))
    v = Verdict(answer=answer, facts=cited)
    if not cited or not q.get("answer"):
        return v                                      # an answer resting on no fact knows nothing
    if known := (memo or {}).get(_graded(q["question"], q["answer"], v.answer)):
        v.points, v.conflict = known
        return v
    g = await _ask(Grade, COMPARE, f"QUESTION: {q['question']}\nREFERENCE: {q['answer']}\nANSWER: {v.answer}",
                   exact=True)
    v.points, v.conflict = g.points, g.conflict
    return v


def _status(score: int | None) -> str:
    if score is None:
        return "unset"
    return "answered" if score >= ANSWERED else "partial" if score >= PARTIAL else "open"


def _evidence(q: dict, found: list[dict]) -> str:
    """What a verdict follows from: the question, its reference and the facts shown."""
    return hashlib.sha1(json.dumps([q["question"], q.get("answer", ""), [f["fact"] for f in found]],
                                   ensure_ascii=False).encode()).hexdigest()[:16]


def _kept(item: dict, found: list[dict]) -> Verdict:
    """The last sitting's verdict on the same evidence."""
    return Verdict(points=item["points"], conflict=item["conflict"], answer=item["answer"],
                   facts=[n + 1 for n, f in enumerate(found) if f["uid"] in item["facts"]])


async def take(db: ArcadeDB, domain: str, fresh: bool = False, kind: str = "exam") -> dict:
    """One sitting of the exam or of the practice set (`kind`): every question answered from the graph alone, graded
    against its reference, stored. `fresh`: new questions first. ExamError without a charter; Busy while another
    sitting of the domain runs."""
    if domain in _running:
        raise Busy(f"The exam of {domain} is being taken already")
    _running.add(domain)
    started = time.monotonic()
    try:
        exam = await ensure(db, domain, fresh, kind)
        last = await latest(db, domain, kind)
        before = {x["id"]: x for x in last["items"]} if last and last["version"] == exam["version"] else {}
        memo = await _grades(db, domain, kind)
        items = []
        for i, q in enumerate(exam["questions"]):
            found: list[dict] = []
            evidence = ""
            try:
                with activity.working(domain, "learning", f"exam {i + 1}/{len(exam['questions'])}"):
                    found = (await kg.query(db, domain, q["question"], 8))["facts"][:FACTS_SHOWN]
                    evidence = _evidence(q, found)
                    old = before.get(q["id"])
                    v = (_kept(old, found) if old and old.get("evidence") == evidence
                         and old.get("grader", GRADER) == GRADER else await _grade(domain, q, found, memo))
            except Exception as e:   # one question that can't be graded scores nothing; the sitting goes on
                print(f"[Exam] {domain}: {q['id']} not graded: {type(e).__name__}: {e}"[:300], flush=True)
                evidence = ""        # graded again next time
                v = Verdict(answer=f"Not graded ({type(e).__name__})")
            cited = [found[n - 1] for n in v.facts]
            if not evidence and i + 1 >= 3 and all(not x["evidence"] for x in items):
                # Nothing graded so far: the model (or the graph) is out of reach, and a sitting of zeros would stand
                # in the scores and send research everywhere — 2026-11-07, the model killed for memory: 0/100, every
                # one of 30 questions "not graded (ConnectError)". Not stored; the next sitting measures.
                raise ExamError(f"The first {i + 1} questions could not be graded ({v.answer}) — the sitting is "
                                "called off, not scored")
            items.append({"id": q["id"], "core": q["core"], "tier": q.get("tier"), "question": q["question"],
                          "reference": q.get("answer", ""), "source": q.get("source"), "points": v.points,
                          "conflict": v.conflict, "answer": v.answer.strip(), "facts": [f["uid"] for f in cited],
                          "sources": len({s for f in cited for s in f.get("sources") or []}),
                          "well_sourced": any((f.get("evidence") or 0) >= 2 for f in cited), "evidence": evidence,
                          "grader": GRADER})
        if (ungraded := sum(not x["evidence"] for x in items)) * 2 > len(items):
            raise ExamError(f"{ungraded} of {len(items)} questions could not be graded — the sitting is called off, "
                            "not scored")
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
               "seconds": round(time.monotonic() - started), "n": time.time_ns(),   # n orders sittings
               **({"kind": kind} if kind != "exam" else {})}
        return await kgdb.insert_doc(db, domain, "ExamRun", run, RUN_JSON)
    finally:
        _running.discard(domain)


def start(db: ArcadeDB, domain: str, fresh: bool = False, kinds: tuple[str, ...] = ("practice", "exam")) -> bool:
    """Sittings in the background, one after the other (e.g. the baselines when a charter is confirmed). False if one
    is running."""
    if domain in _running:
        return False

    async def sit() -> None:
        for kind in kinds:
            try:
                run = await take(db, domain, fresh, kind)
                print(f"[Exam] {domain} {kind}: {run['score']}/100 ({run['answered']} answered, {run['open']} open)",
                      flush=True)
            except Exception as e:
                print(f"[Exam] {domain} {kind} failed: {type(e).__name__}: {e}", flush=True)

    task = asyncio.get_running_loop().create_task(sit())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


# ── Reading the results ────────────────────────────────────────────────────

def _of(kind: str) -> str:
    return "kind = 'practice'" if kind == "practice" else "kind IS NULL"   # the exam's sittings predate `kind`


async def history(db: ArcadeDB, domain: str, limit: int = 60, kind: str = "exam") -> list[dict]:
    """Sittings, oldest first, without their answers: {uid, created_at, version, score, answered, partial, open,
    total, graph}."""
    rows = await kgdb.find_docs(db, domain, "ExamRun", ("graph",), _of(kind), order="n DESC", limit=limit)
    keep = ("uid", "created_at", "version", "score", "answered", "partial", "open", "total", "graph")
    return [{k: r.get(k) for k in keep} for r in reversed(rows)]


async def latest(db: ArcadeDB, domain: str, kind: str = "exam") -> dict | None:
    rows = await kgdb.find_docs(db, domain, "ExamRun", RUN_JSON, _of(kind), order="n DESC", limit=1)
    return rows[0] if rows else None


async def open_questions(db: ArcadeDB, domain: str, n: int = 8) -> list[str]:
    """The practice questions the graph could not answer at its latest practice sitting (then the half-answered):
    where research should go first. Never the exam's — research aimed at them would teach to the test."""
    run, exam = await latest(db, domain, "practice"), await current(db, domain, "practice")
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
