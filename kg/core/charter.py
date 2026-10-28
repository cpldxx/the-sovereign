"""The research charter — what a domain's user really wants to know, kept for good. Every research run steers by it.

    interview  with no charter yet the Head interviews the user (its prompt says so) and, once it understands, drafts
               the charter from its notes (draft) — the Head's draft_charter tool, or Settings › Charter
    confirm    the user agrees (in the chat: the Head's confirm_charter; or Settings): the charter is active and the
               domain's first deep research starts from it — a bootstrap if the domain has none, and a mission for
               each core question (the research service's kickoff)
    steer      brief(): the description plus the active charter, for every prompt that used to get the description
               alone — research missions, the Strategist's questions, the ontology, the Head
    grow       suggest(): the Head proposes an addition when the user keeps asking about something the charter lacks;
               the user takes or leaves it (decide). Edits apply at once.

Stored as the Setting document "charter" in the domain's database: {charter, status, suggestions, …}.
status: "draft" until confirmed, then "active" for good (a later revision steers at once — the user asked for it).
"""

import os
import re
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from core import auth
from core import database as kgdb
from core.database import ArcadeDB, new_uid, now
from core.llm import model_for, model_settings
from domains import registry

DRAFT, ACTIVE = "draft", "active"
DEPTHS = {
    "overview": "the big picture: what it is, who matters, where it is going",
    "working": "enough to discuss it and act on it: the main findings, numbers and debates",
    "expert": "specialist depth: primary literature, methods, evidence quality and open debates",
}
KINDS = ("question", "scope", "exclude")
LIMITS = {"goal": 600, "item": 300, "questions": 12, "scope": 20, "exclude": 20, "suggestions": 10}
DRAFTED_QUESTIONS = 8   # a model's draft keeps its first 8 (asked for 3-8, it wrote 10); a person may add up to 12
_FIELD = {"question": "questions", "scope": "scope", "exclude": "exclude"}

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
KICKOFF = int(os.getenv("CHARTER_KICKOFF_QUESTIONS", "5"))   # core questions researched right away; the rest by cycles
MODEL = model_for("strategist")


class CharterError(ValueError):
    pass


class Charter(BaseModel):
    goal: str = Field(description="What the user wants to understand or decide, and why — one to three sentences")
    questions: list[str] = Field(description="3-8 core questions this domain's knowledge must answer, most important "
                                             "first; each specific and self-contained")
    scope: list[str] = Field(default_factory=list, description="What belongs: sub-topics, periods, regions, kinds "
                                                               "of sources — short phrases")
    exclude: list[str] = Field(default_factory=list, description="What the user wants left out — short phrases")
    depth: Literal["overview", "working", "expert"] = Field("working", description="How deep the user wants to go")


PROMPT = """You write the research charter of the "{domain}" knowledge domain: what its user really wants to know.
It steers every research run from now on. Domain: {description}
Today is {today}.

From the notes below write:
- goal: what the user wants to understand or decide, and why — one to three sentences, in the user's own voice
  ("I want to understand…")
- questions: 3-8 core questions the domain's knowledge must be able to answer, the ones the user cares about most
  first. Each is one short sentence: specific, self-contained, answerable by research (names, periods where they
  matter).
- scope: what belongs — sub-topics, periods, regions, kinds of sources (short phrases)
- exclude: what the user wants left out (short phrases); empty when they named nothing
- depth: overview (the big picture), working (enough to discuss and act on it) or expert (specialist depth: primary
  literature, methods, open debates)
Write in the language of the notes, and only in it: Korean notes get Korean with no Chinese characters or Japanese
(proper and product names stay as written). Use only what the notes and the description support: leave scope or
exclude short rather than invent preferences the user never stated.{current}"""

_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_FOREIGN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")   # kana and Chinese characters


# ── Storage ────────────────────────────────────────────────────────────────

async def _doc(db: ArcadeDB, domain: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key="charter")
    return rows[0] if rows else None


async def get(db: ArcadeDB, domain: str) -> dict:
    """{charter, status: none|draft|active, suggestions, updated_at, confirmed_at}."""
    doc = await _doc(db, domain) if await db.exists(domain) else None
    value = (doc or {}).get("value") or {}
    return {"charter": value.get("charter"), "status": value.get("status") or "none",
            "suggestions": value.get("suggestions") or [], "updated_at": value.get("updated_at"),
            "confirmed_at": value.get("confirmed_at")}


async def _put(db: ArcadeDB, domain: str, state: dict) -> dict:
    value = {k: state.get(k) for k in ("charter", "status", "suggestions", "confirmed_at")}
    value["updated_at"] = now()
    doc = await _doc(db, domain)
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "charter", "value": value}, ("value",))
    return await get(db, domain)


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _items(value, field: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise CharterError(f"{field}: a list of short texts")
    items = list(dict.fromkeys(c for c in map(_clean, value) if c))
    if too_long := [v for v in items if len(v) > LIMITS["item"]]:
        raise CharterError(f"{field}: each at most {LIMITS['item']} characters ({too_long[0][:40]}… is longer)")
    if len(items) > LIMITS[field]:
        raise CharterError(f"{field}: at most {LIMITS[field]}")
    return items


def check(changes: dict) -> dict:
    """The valid charter fields out of `changes`, or CharterError saying what is wrong."""
    out = {}
    if "goal" in changes:
        goal = _clean(changes["goal"])
        if len(goal) > LIMITS["goal"]:
            raise CharterError(f"goal: at most {LIMITS['goal']} characters")
        out["goal"] = goal
    for field in ("questions", "scope", "exclude"):
        if field in changes:
            out[field] = _items(changes[field], field)
    if "depth" in changes:
        if changes["depth"] not in DEPTHS:
            raise CharterError(f"depth: one of {', '.join(DEPTHS)}")
        out["depth"] = changes["depth"]
    return out


def _fit(c: dict) -> dict:
    """A model's charter within the limits — cut to fit, not refused."""
    out = {"goal": _clean(c.get("goal"))[:LIMITS["goal"]],
           "depth": c.get("depth") if c.get("depth") in DEPTHS else "working"}
    for field in ("questions", "scope", "exclude"):
        out[field] = list(dict.fromkeys(x for x in (_clean(v)[:LIMITS["item"]] for v in c.get(field) or []) if x))
        out[field] = out[field][:LIMITS[field]]
    return out


async def save(db: ArcadeDB, domain: str, changes: dict) -> dict:
    """Edit the charter (a person in Settings). A charter must keep a goal and a question."""
    state = await get(db, domain)
    charter = {**(state["charter"] or Charter(goal="", questions=[]).model_dump()), **check(changes)}
    if not charter["goal"] or not charter["questions"]:
        raise CharterError("A charter needs a goal and at least one core question")
    return await _put(db, domain, {**state, "charter": charter, "status": state["status"] if state["charter"] else DRAFT})


async def draft(db: ArcadeDB, domain: str, notes: str = "") -> dict:
    """Write the charter from interview notes (none: from the description alone). An active charter is revised —
    it stays active and steers by the revision at once; otherwise it is a draft until confirmed."""
    state = await get(db, domain)
    description = registry.load_domain(domain)["config"].get("description") or domain
    current = (f"\n\nThe current charter, to revise with the notes (keep what they don't change):\n{text(state['charter'])}"
               if state["charter"] else "")
    agent = Agent(MODEL, name="charter", model_settings=model_settings(), output_type=Charter, retries=2,
                  system_prompt=PROMPT.format(domain=domain, description=description, today=date.today().isoformat(),
                                              current=current))

    @agent.output_validator
    def one_language(c: Charter) -> Charter:
        # A local model lets Chinese characters into Korean (measured 2026-10-28: 各社, 半导体): the schema
        # can't say "one language", so the validator does — and the model writes it again.
        written = " ".join([c.goal, *c.questions, *c.scope, *c.exclude])
        if _HANGUL.search(notes + written) and (foreign := sorted(set(_FOREIGN.findall(written)))):
            raise ModelRetry(f"Write it in Korean only — no Chinese characters or Japanese ({''.join(foreign[:12])}); "
                             "use the Korean word instead.")
        return c
    written = (await agent.run(notes.strip() or "(No interview notes: draft it from the description alone, "
                                                "conservatively.)")).output
    charter = _fit(written.model_dump())
    charter["questions"] = charter["questions"][:DRAFTED_QUESTIONS]
    if not charter["questions"]:
        raise CharterError("The draft had no core question — tell the Head more about what you want to find out")
    return await _put(db, domain, {**state, "charter": charter, "status": state["status"] if state["status"] == ACTIVE else DRAFT})


async def confirm(db: ArcadeDB, domain: str) -> tuple[dict, bool]:
    """The user agrees: the charter is active. (state, first) — first: it just became active (the first deep research
    is due)."""
    state = await get(db, domain)
    if not state["charter"]:
        raise CharterError("There is no charter to confirm yet — the Head drafts one after a short interview")
    if state["status"] == ACTIVE:
        return state, False
    return await _put(db, domain, {**state, "status": ACTIVE, "confirmed_at": now()}), True


async def start(db: ArcadeDB, domain: str) -> dict:
    """Confirm, and — the first time — start the domain's first deep research. {…state, kickoff: what was queued,
    or why nothing was}."""
    state, first = await confirm(db, domain)
    if not first:
        return {**state, "kickoff": None}
    try:
        async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=30, headers=auth.headers(domain)) as client:
            r = await client.post(f"/domains/{domain}/kickoff", json={"questions": state["charter"]["questions"][:KICKOFF]})
        r.raise_for_status()
        return {**state, "kickoff": r.json()}
    except httpx.HTTPError as e:
        # Confirmed all the same: the next learning cycle researches from the charter.
        return {**state, "kickoff": {"error": f"the research service did not take it ({type(e).__name__}) — the next "
                                              "learning cycle starts from the charter"}}


async def reset(db: ArcadeDB, domain: str) -> dict:
    """Start over: no charter (the Head interviews again). The graph keeps everything it learned."""
    state = await get(db, domain)
    return await _put(db, domain, {**state, "charter": None, "status": "none", "suggestions": [], "confirmed_at": None})


# ── Growing it ─────────────────────────────────────────────────────────────

async def suggest(db: ArcadeDB, domain: str, kind: str, item: str, why: str = "") -> dict:
    """The Head proposes an addition; the user decides. A suggestion the charter or the waiting list has is skipped."""
    if kind not in KINDS:
        raise CharterError(f"kind: one of {', '.join(KINDS)}")
    item = _clean(item)
    if not item or len(item) > LIMITS["item"]:
        raise CharterError(f"A suggestion is a short text (at most {LIMITS['item']} characters)")
    state = await get(db, domain)
    if not state["charter"]:
        raise CharterError("There is no charter yet — draft one first")
    have = {x.casefold() for x in state["charter"][_FIELD[kind]]} | {s["text"].casefold() for s in state["suggestions"]}
    if item.casefold() in have:
        return state
    if len(state["suggestions"]) >= LIMITS["suggestions"]:
        raise CharterError(f"{LIMITS['suggestions']} suggestions already wait for the user — let them decide first")
    suggestion = {"uid": new_uid("cs"), "kind": kind, "text": item, "why": " ".join(why.split())[:300], "at": now()}
    return await _put(db, domain, {**state, "suggestions": state["suggestions"] + [suggestion]})


async def decide(db: ArcadeDB, domain: str, uid: str, accept: bool) -> dict:
    state = await get(db, domain)
    suggestion = next((s for s in state["suggestions"] if s["uid"] == uid), None)
    if not suggestion:
        raise CharterError(f"No suggestion {uid} waits")
    rest = [s for s in state["suggestions"] if s["uid"] != uid]
    charter = state["charter"]
    if accept and charter:
        field = _FIELD[suggestion["kind"]]
        charter = {**charter, field: _items(charter[field] + [suggestion["text"]], field)}
    return await _put(db, domain, {**state, "charter": charter, "suggestions": rest})


# ── Steering ───────────────────────────────────────────────────────────────

def text(charter: dict | None) -> str:
    """A charter as prompts read it."""
    if not charter:
        return ""
    lines = [f"Goal: {charter['goal']}", "Core questions:", *(f"- {q}" for q in charter["questions"])]
    if charter.get("scope"):
        lines.append("In scope: " + "; ".join(charter["scope"]))
    if charter.get("exclude"):
        lines.append("Leave out: " + "; ".join(charter["exclude"]))
    depth = charter.get("depth") or "working"
    lines.append(f"Depth: {depth} — {DEPTHS.get(depth, '')}")
    return "\n".join(lines)


async def brief(db: ArcadeDB, domain: str) -> str:
    """The domain as research prompts should see it: its description, and the user's charter once confirmed."""
    description = registry.load_domain(domain)["config"].get("description") or domain
    state = await get(db, domain)
    if state["status"] != ACTIVE:
        return description
    return (f"{description}\n\nResearch charter — what the user wants from this domain. Serve its core questions "
            f"first and never research what it says to leave out (it may be in another language: search in English "
            f"too):\n{text(state['charter'])}")
