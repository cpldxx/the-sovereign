"""Playbooks — the slow path's pre-computed decisions, and how new knowledge triggers them.

Nightly cycle (after the daily report):
  evaluate  each active playbook against the period's changes on the entities it watches (the Watcher LLM
            runs only when there are such changes) → a proposal of its action, citing the new facts
            (alerts run at once; external actions wait for the user)
  refresh   the Strategist rewrites the playbook set from the graph as it now is: keep / revise / drop / add
Evaluate runs first: playbooks are the expectations formed before today's news — refreshed first, they would
be derived from the very facts they are then checked against.
"""

import time
from datetime import datetime, timedelta, timezone

from agents.strategist import write_playbooks
from agents.watcher import judge
from core import actions
from core import database as kgdb
from core.database import ArcadeDB, now
from core.names import name_key
from core.tracing import observe
from domains.registry import load_domain

PLAYBOOK_JSON = ("watch", "evidence")
FACTS_PER_VERDICT = 40

_running: set[str] = set()


def running(domain: str) -> bool:
    return domain in _running


async def list_playbooks(db: ArcadeDB, domain: str, status: str | None = "active") -> list[dict]:
    if status:
        return await kgdb.find_docs(db, domain, "Playbook", PLAYBOOK_JSON, "status = :status", status=status)
    return await kgdb.find_docs(db, domain, "Playbook", PLAYBOOK_JSON)


async def set_status(db: ArcadeDB, domain: str, uid: str, status: str) -> bool:
    return await kgdb.update_doc(db, domain, "Playbook", uid, {"status": status, "updated_at": now()})


# ── Refresh (Strategist) ───────────────────────────────────────────────────

async def _context(db: ArcadeDB, domain: str, description: str, catalog: list[dict], current: list[dict]) -> str:
    entities = await kgdb.top_entities(db, domain, 40)
    facts = await kgdb.top_facts(db, domain, 60)
    superseded = await kgdb.top_facts(db, domain, 20, valid=False)
    lines = [f"DOMAIN: {domain} — {description}", "", "ACTIONS:"]
    lines += [f"- {a['name']} ({'needs confirmation' if a['confirm'] else 'runs at once'}): {a['description']}"
              for a in catalog]
    lines += ["", "EXISTING PLAYBOOKS:"] + ([
        f"- uid={p['uid']} | {p['name']} | when: {p['situation']} | watch: {', '.join(w['name'] for w in p['watch'])}"
        f" | do: {p['action']} — {p['response']}" for p in current] or ["(none)"])
    lines += ["", "KEY ENTITIES (most mentioned):"]
    lines += [f"- {e['name']} ({e['type']}): {e.get('summary') or ''}" for e in entities]
    lines += ["", "CURRENT FACTS (strongest first):"]
    lines += [f"- uid={f['uid']} ({f['weight']:.2f}) {f['fact']}" for f in facts]
    lines += ["", "RECENTLY SUPERSEDED (how things have been changing):"]
    lines += [f"- WAS: {f['fact']} — {f.get('invalid_reason') or ''}" for f in superseded]
    return "\n".join(lines)


async def refresh(db: ArcadeDB, domain: str) -> dict:
    """Rewrite the domain's playbooks from the graph. On failure the current ones stay."""
    description = load_domain(domain)["config"].get("description") or domain
    catalog = await actions.catalog(db, domain)
    current = {p["uid"]: p for p in await list_playbooks(db, domain)}
    with observe("playbooks_refresh", metadata={"domain": domain}):
        drafts = await write_playbooks(await _context(db, domain, description, catalog, list(current.values())))
    names = {a["name"] for a in catalog}
    counts = {"kept": 0, "created": 0, "retired": 0}
    kept: set[str] = set()
    for d in drafts:
        watch = []
        for name in dict.fromkeys(d.watch):
            if (key := name_key(name)) and (match := await kgdb.entities_by_keys(db, domain, [key])):
                watch.append({"uid": match[0]["uid"], "name": match[0]["name"]})
        if not watch:
            continue  # nothing to watch: it could never trigger
        evidence = [f["uid"] for f in await kgdb.get_facts(db, domain, d.evidence[:20])]
        doc = {"name": d.name.strip(), "situation": d.situation.strip(), "watch": watch, "response": d.response.strip(),
               "action": d.action if d.action in names else "alert", "evidence": evidence, "updated_at": now()}
        if d.uid in current and d.uid not in kept:
            await kgdb.update_doc(db, domain, "Playbook", d.uid, doc, PLAYBOOK_JSON)
            kept.add(d.uid)
            counts["kept"] += 1
        else:
            await kgdb.insert_doc(db, domain, "Playbook", {**doc, "status": "active", "fired": 0, "last_fired_at": None},
                                  PLAYBOOK_JSON)
            counts["created"] += 1
    for uid in set(current) - kept:
        await set_status(db, domain, uid, "retired")
        counts["retired"] += 1
    return counts


# ── Evaluate (Watcher) ─────────────────────────────────────────────────────

async def evaluate(db: ArcadeDB, domain: str, since: str) -> dict:
    """Check every active playbook against the facts that changed since `since`; propose the triggered ones."""
    changes = await kgdb.changes_since(db, domain, since)
    changed = ([{**f, "change": "new"} for f in changes["facts_created"]]
               + [{**f, "change": "confirmed by another source"} for f in changes["facts_strengthened"]]
               + [{**f, "change": "superseded"} for f in changes["facts_invalidated"]])
    catalog = {a["name"]: a for a in await actions.catalog(db, domain)}
    result = {"evaluated": 0, "triggered": 0, "proposals": []}
    for pb in await list_playbooks(db, domain):
        watched = {w["uid"] for w in pb["watch"]}
        facts = [f for f in changed if f["source_uid"] in watched or f["target_uid"] in watched]
        if not facts or (pb.get("last_fired_at") or "") >= since:
            continue
        action = catalog.get(pb["action"]) or catalog["alert"]
        result["evaluated"] += 1
        verdict = await judge(pb, action, facts[:FACTS_PER_VERDICT])
        if not verdict.triggered:
            continue
        known = {f["uid"] for f in facts}
        evidence = [u for u in verdict.evidence if u in known] or [f["uid"] for f in facts[:5]]
        rationale = f"Playbook “{pb['name']}”: {verdict.rationale}"
        try:
            proposal = await actions.propose(db, domain, action["name"], verdict.params, rationale, evidence,
                                             source="playbook", playbook_uid=pb["uid"])
        except actions.ActionError:
            # The Watcher filled the action badly: still tell the owner.
            proposal = await actions.propose(db, domain, "alert", {"title": pb["name"], "message": verdict.rationale,
                                                                   "severity": "warning"},
                                             rationale, evidence, source="playbook", playbook_uid=pb["uid"])
        await kgdb.update_doc(db, domain, "Playbook", pb["uid"], {"last_fired_at": now(),
                                                                  "fired": (pb.get("fired") or 0) + 1})
        result["triggered"] += 1
        result["proposals"].append({"uid": proposal["uid"], "action": proposal["action"],
                                    "status": proposal["status"], "playbook": pb["name"]})
    return result


async def cycle(db: ArcadeDB, domain: str, hours: int = 24, refresh_after: bool = True) -> dict:
    """Nightly: evaluate the playbooks against the last `hours`, then refresh them. Raises ValueError for unknown
    domains and RuntimeError when a cycle for the domain is already running."""
    load_domain(domain)
    if domain in _running:
        raise RuntimeError(f"A playbook cycle for {domain} is already running")
    _running.add(domain)
    started = time.monotonic()
    try:
        with observe("playbooks", input={"hours": hours}, metadata={"domain": domain}, tags=[domain, "playbooks"]):
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
            evaluated = await evaluate(db, domain, since)
            refreshed = await refresh(db, domain) if refresh_after else None
        return {"evaluate": evaluated, "refresh": refreshed, "seconds": round(time.monotonic() - started)}
    finally:
        _running.discard(domain)
