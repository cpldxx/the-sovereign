"""The learning cycle — how a domain's graph keeps growing smarter by itself, on a schedule the user sets.

    look     gaps(): what the graph is unsure or silent about — facts resting on one weak source, well-connected
             entities nothing new was heard about lately, questions users asked that the Head had to take to the
             web, words the ontology lacked
    learn    the research service runs "what's new" plus the Strategist's questions for those gaps (research_questions)
             through the usual pipeline: every fact checked against its source, confirmed ones grow stronger
    keep     the Head decides the review queue, the Linker and Summarizer tidy up (nightly report)
    forget   forget(): a fact that rested on one weak source and was never confirmed in `forget_after_days` is
             retired (invalidated, reason "forgotten: …" — still history, never an answer); an entity left with only
             forgotten facts goes. Nothing a pending review or an active playbook relies on.

Settings per domain (a Setting document "learning" in its database): when it runs (times in the user's timezone),
how many questions it pursues per cycle, how long an unconfirmed weak fact may wait. The research service runs the
cycles (research/server.py) and records the last one here.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents.strategist import research_questions
from core import database as kgdb
from core.database import ArcadeDB, now
from domains import registry

# The machine's timezone (the launcher passes it in; containers run on UTC) until the user saves their own.
DEFAULTS = {"enabled": True, "times": ["03:00"], "timezone": os.getenv("SOVEREIGN_TIMEZONE") or "UTC", "curiosity": 2,
            "forget_after_days": 30}
WEAK = 0.5                 # weight below which a single-source fact is weak
STALE_DAYS = 30            # a well-connected entity with nothing new for this long is worth refreshing
FORGOTTEN = "forgotten"    # invalid_reason prefix of retired facts
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LearningError(ValueError):
    pass


async def _doc(db: ArcadeDB, domain: str, key: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Setting", ("value",), "key = :key", limit=1, key=key)
    return rows[0] if rows else None


async def settings(db: ArcadeDB, domain: str) -> dict:
    doc = await _doc(db, domain, "learning")
    return {**DEFAULTS, **((doc or {}).get("value") or {})}


def check(changes: dict) -> dict:
    """Valid settings out of `changes`, or LearningError saying what is wrong."""
    out = {}
    if "enabled" in changes:
        out["enabled"] = bool(changes["enabled"])
    if "times" in changes:
        times = sorted({str(t).strip() for t in changes["times"] or []})
        if bad := [t for t in times if not _TIME.match(t)]:
            raise LearningError(f"times: HH:MM, 24-hour — not {bad}")
        if len(times) > 8:
            raise LearningError("times: at most 8 a day")
        out["times"] = times
    if "timezone" in changes:
        try:
            ZoneInfo(str(changes["timezone"]))
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise LearningError(f"timezone: unknown {changes['timezone']!r}") from e
        out["timezone"] = str(changes["timezone"])
    if "curiosity" in changes:
        if not 0 <= int(changes["curiosity"]) <= 5:
            raise LearningError("curiosity: 0 to 5 questions a cycle")
        out["curiosity"] = int(changes["curiosity"])
    if "forget_after_days" in changes:
        if not 0 <= int(changes["forget_after_days"]) <= 365:
            raise LearningError("forget_after_days: 0 (never) to 365")
        out["forget_after_days"] = int(changes["forget_after_days"])
    return out


async def save(db: ArcadeDB, domain: str, changes: dict) -> dict:
    new = {**await settings(db, domain), **check(changes)}
    doc = await _doc(db, domain, "learning")
    value = {k: new[k] for k in DEFAULTS}
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": value}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "learning", "value": value}, ("value",))
    return {**await settings(db, domain)}


async def record_cycle(db: ArcadeDB, domain: str, summary: dict) -> None:
    doc = await _doc(db, domain, "learning_last")
    if doc:
        await kgdb.update_doc(db, domain, "Setting", doc["uid"], {"value": summary}, ("value",))
    else:
        await kgdb.insert_doc(db, domain, "Setting", {"key": "learning_last", "value": summary}, ("value",))


async def last_cycle(db: ArcadeDB, domain: str) -> dict | None:
    doc = await _doc(db, domain, "learning_last")
    return doc["value"] if doc else None


# ── Look ───────────────────────────────────────────────────────────────────

async def gaps(db: ArcadeDB, domain: str, user: str | None = None) -> dict:
    """What the graph is unsure or silent about — the raw material of the next cycle's questions.
    `user`: show only that person's own unanswered questions (unanswered_total still counts everyone's)."""
    if not await db.exists(domain):
        return {"weak": [], "stale": [], "unanswered": [], "unanswered_total": 0, "ontology": []}
    before = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
    weak = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NULL AND r.evidence <= 1 AND r.disbelief > $d "
        "RETURN a.name AS name, count(r) AS n ORDER BY n DESC LIMIT 8", d=1 - 0.6)
    stale = await db.cypher(
        domain,
        "MATCH (e:Entity)-[r:Fact]-() WHERE r.invalid_at IS NULL WITH e, count(r) AS degree, max(r.updated_at) AS last "
        "WHERE degree >= 5 AND last < datetime($before) RETURN e.name AS name, degree, last ORDER BY degree DESC LIMIT 8",
        before=before)
    # What people asked that the graph couldn't answer: the Head went to the web for it.
    # Each answer pairs with its own asker's question: turns of everyone are read together (2026-10-14: answers were
    # paired with whoever spoke last — another person's question). Everyone's feed the Strategist; a person sees only
    # their own (each person's chat is theirs alone).
    turns = await kgdb.find_docs(db, domain, "ChatTurn", ("steps",), limit=200)
    turns.sort(key=lambda t: t.get("n") or 0)
    unanswered, last = [], {}
    for t in turns:
        prev, last[t.get("user")] = last.get(t.get("user")), t
        if t["role"] == "assistant" and prev and prev["role"] == "user" and any(
                isinstance(s, dict) and s.get("name") in ("web_search", "read_webpage") for s in t.get("steps") or []):
            unanswered.append((prev.get("user"), (prev.get("content") or "")[:200]))
    gaps_ = {}
    for r in await db.cypher(domain, "MATCH (ep:Episode) WHERE ep.ontology_gaps IS NOT NULL "
                                     "RETURN ep.ontology_gaps AS g ORDER BY ep.created_at DESC LIMIT 50"):
        for k, v in (json.loads(r["g"]) if isinstance(r["g"], str) else r["g"] or {}).items():
            gaps_[k] = gaps_.get(k, 0) + v
    return {"weak": [{"entity": r["name"], "weak_facts": r["n"]} for r in weak],
            "stale": [{"entity": r["name"], "facts": r["degree"], "last": str(r["last"])[:10]} for r in stale],
            "unanswered": [q for who, q in unanswered if user is None or who == user][-8:],
            "unanswered_total": len(unanswered),
            "ontology": [k for k, _ in sorted(gaps_.items(), key=lambda kv: -kv[1])[:8]]}


def _gap_text(g: dict) -> str:
    lines = ["Facts resting on one weak source, by entity: "
             + (", ".join(f"{w['entity']} ({w['weak_facts']})" for w in g["weak"]) or "none"),
             "Well-connected entities with nothing new for a month: "
             + (", ".join(f"{s['entity']} (last {s['last']})" for s in g["stale"]) or "none"),
             "Questions users asked that the graph could not answer:"
             + ("".join(f"\n- {q}" for q in g["unanswered"]) or " none"),
             "What sources said that the ontology had no words for: " + (", ".join(g["ontology"]) or "nothing")]
    return "\n".join(lines)


async def questions(db: ArcadeDB, domain: str, n: int) -> dict:
    """The Strategist's research questions for the current gaps (none when n is 0 or nothing matters)."""
    g = await gaps(db, domain)
    if n <= 0 or not any(g.values()):
        return {"questions": [], "gaps": g}
    description = registry.load_domain(domain)["config"].get("description") or domain
    out = await research_questions(domain, description, _gap_text(g), n)
    return {"questions": [q.model_dump() for q in out.questions], "gaps": g}


# ── Forget ─────────────────────────────────────────────────────────────────

async def forget(db: ArcadeDB, domain: str, days: int, dry_run: bool = False) -> dict:
    """Retire facts that rested on one weak source and were never confirmed in `days`; delete the entities left
    with nothing but forgotten facts. Spares what a pending review or a playbook relies on."""
    if days <= 0 or not await db.exists(domain):
        return {"facts": 0, "entities": 0, "examples": []}
    before = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    keep = await kgdb.referenced(db, domain)   # pending reviews (nested values too), active playbooks
    rows = await db.cypher(
        domain,
        "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.invalid_at IS NULL AND r.evidence <= 1 AND r.disbelief > $d "
        "AND r.created_at < datetime($before) RETURN r.uid AS uid, r.fact AS fact, a.uid AS a, b.uid AS b",
        d=1 - WEAK, before=before)
    doomed = [r for r in rows if r["uid"] not in keep]
    gone = {r["uid"] for r in doomed}
    # Entities left with nothing but forgotten facts: nothing true is known about them any more. (A superseded
    # fact is history worth keeping; it keeps its entity.)
    touched = sorted(({r["a"] for r in doomed} | {r["b"] for r in doomed}) - keep)
    entities = []
    if touched:
        facts = await db.cypher(domain, "MATCH (e:Entity)-[r:Fact]-() WHERE e.uid IN $uids "
                                        "RETURN e.uid AS e, r.uid AS uid, r.invalid_reason AS why", uids=touched)
        kept = {f["e"] for f in facts if f["uid"] not in gone and not (f["why"] or "").startswith(FORGOTTEN)}
        entities = [u for u in touched if u not in kept]
    if not dry_run:
        why = f"{FORGOTTEN}: a single weak source, not confirmed in {days} days"
        for i in range(0, len(doomed), 200):
            await db.cypher(domain, "MATCH ()-[r:Fact]->() WHERE r.uid IN $uids SET r.invalid_at = $now, "
                                    "r.invalid_reason = $why", uids=[r["uid"] for r in doomed[i:i + 200]], now=now(), why=why)
        if entities:
            await db.cypher(domain, "MATCH (e:Entity) WHERE e.uid IN $uids DETACH DELETE e", uids=entities)
    return {"facts": len(doomed), "entities": len(entities), "examples": [r["fact"] for r in doomed[:5]]}


# ── When ───────────────────────────────────────────────────────────────────

WINDOW = 30   # minutes a slot stays due: a busy minute isn't a missed run


def due(s: dict, at: datetime, done: set[str]) -> str | None:
    """The slot ("2026-10-13 03:00") due at `at` (UTC) for these settings, unless already in `done`. A window runs
    past midnight (2026-10-14: 23:59 never became due, 23:45 had 14 minutes); its slot keeps the day it started."""
    if not s.get("enabled"):
        return None
    local = at.astimezone(ZoneInfo(s.get("timezone") or "UTC"))
    minute = local.hour * 60 + local.minute
    for t in s.get("times") or []:
        h, m = map(int, t.split(":"))
        since = (minute - (h * 60 + m)) % (24 * 60)
        if since < WINDOW:
            day = local.date() - timedelta(days=1) if since > minute else local.date()
            slot = f"{day.isoformat()} {t}"
            if slot not in done:
                return slot
    return None
