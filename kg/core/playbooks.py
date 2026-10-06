"""Playbooks — the slow path's pre-computed decisions, and how new knowledge triggers them.

A playbook can also carry a live TRIGGER — a condition on a sensor reading (NVDA change_percent < -5, checked
every 60 min). check_triggers() runs around the clock in the KG process: the comparison is code; the Watcher
only fills in the action's parameters once the condition holds.

Nightly cycle (after the daily report):
  evaluate  each active playbook against the period's changes on the entities it watches (the Watcher LLM
            runs only when there are such changes) → a proposal of its action, citing the new facts
            (alerts run at once; external actions wait for the user)
  refresh   the Strategist revises the playbook set from the graph as it now is: add / revise / retire (by name —
            playbooks it doesn't mention stay)
Evaluate runs first: playbooks are the expectations formed before today's news — refreshed first, they would
be derived from the very facts they are then checked against.
"""

import asyncio
import json
import operator
import os
import time
from datetime import datetime, timedelta, timezone

from agents.strategist import MAX_PLAYBOOKS, write_playbooks
from agents.watcher import judge
from core import actions, sensors
from core import database as kgdb
from core.database import ArcadeDB, now
from core.names import name_key
from core.tracing import observe
from domains.registry import list_domains, load_domain

PLAYBOOK_JSON = ("watch", "evidence", "trigger")
OPS = {"<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}
REFIRE_HOURS = 24  # a triggered playbook stays quiet this long
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

def value_at(data, path: str):
    """'quotes.0.price' → data["quotes"][0]["price"]; None if the path doesn't exist."""
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def numeric_fields(data, prefix: str = "", depth: int = 0) -> list[str]:
    """Paths to the numbers in a sensor's output (first item of lists) — what a trigger can compare."""
    if isinstance(data, bool) or depth > 3:
        return []
    if isinstance(data, (int, float)):
        return [prefix]
    if isinstance(data, list):
        return numeric_fields(data[0], f"{prefix}.0" if prefix else "0", depth + 1) if data else []
    if isinstance(data, dict):
        return [p for k, v in data.items() for p in numeric_fields(v, f"{prefix}.{k}" if prefix else k, depth + 1)]
    return []


def _sample(sensor: dict):
    try:
        return json.loads(sensor.get("sample") or "null")
    except ValueError:
        return None


def _sensor_line(sensor: dict) -> str:
    params = ", ".join(f"{k}: {v.get('description', '')}" for k, v in sensor["params"].items())
    fields = ", ".join(numeric_fields(_sample(sensor))[:25]) or "none"
    return f"- {sensor['name']}({params}): {sensor['description']} — numeric fields: {fields}"


def _check_trigger(t, sensor_index: dict) -> dict | None:
    """A Strategist trigger made safe to run: known sensor and parameters, a numeric field present in the sensor's
    sample output, a known comparison. None when it doesn't hold up."""
    sensor = sensor_index.get(t.sensor)
    if not sensor or t.op not in OPS or set(t.params) - set(sensor["params"]):
        return None
    sample = value_at(_sample(sensor), t.field)
    if not isinstance(sample, (int, float)):
        return None
    # A threshold of another scale than the live value is a confusion (a "price >= 1e12" meant market cap).
    if abs(t.value) > 1000 * max(abs(sample), 1):
        return None
    return {"sensor": t.sensor, "params": t.params, "field": t.field, "op": t.op, "value": t.value,
            "every_minutes": max(15, min(int(t.every_minutes or 60), 1440))}


async def _context(db: ArcadeDB, domain: str, description: str, catalog: list[dict], current: list[dict],
                   sensor_list: list[dict]) -> str:
    entities = await kgdb.top_entities(db, domain, 40)
    facts = await kgdb.top_facts(db, domain, 60)
    superseded = await kgdb.top_facts(db, domain, 20, valid=False)
    lines = [f"DOMAIN: {domain} — {description}", "", "ACTIONS:"]
    lines += [f"- {a['name']} ({'needs confirmation' if a['confirm'] else 'runs at once'}): {a['description']}"
              for a in catalog]
    lines += ["", "SENSORS (live data; a trigger may compare one of the listed numeric fields — set the params "
                  "for the entity it is about, e.g. its ticker):"] + ([_sensor_line(x) for x in sensor_list] or ["(none)"])
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
    sensor_list = [x for x in await sensors.list_sensors(db, domain) if x["status"] == "active"]
    with observe("playbooks_refresh", metadata={"domain": domain}):
        answer = await write_playbooks(await _context(db, domain, description, catalog, list(current.values()),
                                                      sensor_list))
    sensor_index = {x["name"]: x for x in sensor_list}
    names = {a["name"] for a in catalog}
    counts = {"revised": 0, "created": 0, "retired": 0}
    kept: set[str] = set()
    # Retire only what the Strategist names: one terse answer from a local model must not wipe the set.
    for uid in answer.retire:
        if uid in current:
            await set_status(db, domain, uid, "retired")
            current.pop(uid)
            counts["retired"] += 1
    room = MAX_PLAYBOOKS - len(current)
    for d in answer.playbooks:
        watch = []
        for name in dict.fromkeys(d.watch):
            if (key := name_key(name)) and (match := await kgdb.entities_by_keys(db, domain, [key])):
                watch.append({"uid": match[0]["uid"], "name": match[0]["name"]})
        if not watch:
            continue  # nothing to watch: it could never trigger
        evidence = [f["uid"] for f in await kgdb.get_facts(db, domain, d.evidence[:20])]
        doc = {"name": d.name.strip(), "situation": d.situation.strip(), "watch": watch, "response": d.response.strip(),
               "action": d.action if d.action in names else "alert", "evidence": evidence, "updated_at": now(),
               "trigger": _check_trigger(d.trigger, sensor_index) if d.trigger else None}
        if d.uid in current and d.uid not in kept:
            await kgdb.update_doc(db, domain, "Playbook", d.uid, doc, PLAYBOOK_JSON)
            kept.add(d.uid)
            counts["revised"] += 1
        elif room > 0:
            await kgdb.insert_doc(db, domain, "Playbook", {**doc, "status": "active", "fired": 0, "last_fired_at": None},
                                  PLAYBOOK_JSON)
            room -= 1
            counts["created"] += 1
    counts["unchanged"] = len(set(current) - kept)
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


# ── Live triggers (sensors) ────────────────────────────────────────────────

async def _fire(db: ArcadeDB, domain: str, pb: dict, reading: str, why: str) -> dict:
    """Propose a playbook's action for a met live condition. The Watcher fills in the action's parameters."""
    catalog = {a["name"]: a for a in await actions.catalog(db, domain)}
    action = catalog.get(pb["action"]) or catalog["alert"]
    verdict = await judge(pb, action, [{"uid": "live", "weight": 1.0, "change": "live sensor reading", "fact": reading}])
    rationale = f"Playbook “{pb['name']}”: {why}. {verdict.rationale}".strip()
    try:
        return await actions.propose(db, domain, action["name"], verdict.params, rationale, pb["evidence"],
                                     source="playbook", playbook_uid=pb["uid"])
    except actions.ActionError:
        return await actions.propose(db, domain, "alert", {"title": pb["name"], "message": f"{why}. {pb['response']}",
                                                           "severity": "warning"},
                                     rationale, pb["evidence"], source="playbook", playbook_uid=pb["uid"])


async def check_triggers(db: ArcadeDB, domain: str) -> list[dict]:
    """Read the sensors of the playbooks whose live trigger is due; propose those whose condition holds."""
    fired = []
    for pb in await list_playbooks(db, domain):
        t = pb.get("trigger")
        if not t:
            continue
        due = datetime.now(timezone.utc) - timedelta(minutes=t["every_minutes"])
        if (pb.get("trigger_checked_at") or "") > due.isoformat(timespec="seconds"):
            continue
        try:
            out = await sensors.read(db, domain, t["sensor"], t["params"])
        except sensors.SensorError as e:
            out = {"ok": False, "error": str(e)}
        value = value_at(out.get("result"), t["field"]) if out.get("ok") else None
        await kgdb.update_doc(db, domain, "Playbook", pb["uid"], {"trigger_checked_at": now(), "trigger_value": value})
        if not isinstance(value, (int, float)) or not OPS[t["op"]](value, t["value"]):
            continue
        quiet = datetime.now(timezone.utc) - timedelta(hours=REFIRE_HOURS)
        if (pb.get("last_fired_at") or "") > quiet.isoformat(timespec="seconds"):
            continue
        args = ", ".join(f"{k}={v}" for k, v in t["params"].items())
        why = f"live {t['sensor']}({args}) {t['field']} = {value} ({t['op']} {t['value']})"
        proposal = await _fire(db, domain, pb, f"{why}, read {now()}", why)
        await kgdb.update_doc(db, domain, "Playbook", pb["uid"], {"last_fired_at": now(),
                                                                  "fired": (pb.get("fired") or 0) + 1})
        fired.append({"playbook": pb["name"], "proposal": proposal["uid"], "status": proposal["status"]})
    return fired


async def watch_loop(db: ArcadeDB, interval: float = 60) -> None:
    """Around the clock: every `interval` seconds, check the due live triggers of every domain."""
    while True:
        await asyncio.sleep(interval)
        for domain in list_domains():
            try:
                for f in await check_triggers(db, domain):
                    print(f"[KG] playbook fired in {domain}: {f}", flush=True)
            except Exception as e:
                print(f"[KG] trigger check for {domain} failed: {type(e).__name__}: {e}", flush=True)


WATCH = os.getenv("SENSOR_WATCH", "on").lower() in ("1", "on", "true", "yes")
