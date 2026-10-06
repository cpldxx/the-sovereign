"""Actions — the fast path's last step: propose → (user confirms) → execute.

The catalog of what a domain can do:
    built-in   alert     tell the user something (shown in the Actions tab)            internal: runs at once
               draft     write a document for the user to use (memo, email, plan)      internal: runs at once
               research  send the research agent on a mission                          internal: runs at once
    webhooks   user-configured external actions (Slack, n8n, Zapier, a broker or ticket API, …): a URL that
               receives the action's parameters as JSON. External: never runs without the user's confirmation.

Agents (the Head, playbooks) can only PROPOSE. A proposal stores the exact parameters and a dry-run preview
of what will happen; confirming or rejecting it is the user's call through the REST API / UI — no agent
tool exists for it. A confirmed proposal runs exactly as previewed, once. Proposals expire after
PROPOSAL_TTL hours: a decision made on stale knowledge should be re-proposed, not executed.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from core import database as kgdb
from core.database import ArcadeDB, now

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
PROPOSAL_TTL = float(os.getenv("PROPOSAL_TTL_HOURS", "24"))
WEBHOOK_TIMEOUT = 20

ACTION_JSON = ("params",)
PROPOSAL_JSON = ("params", "evidence", "result")
_NAME = re.compile(r"[a-z][a-z0-9_]{1,40}")
_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool}

BUILTIN = {
    "alert": {
        "description": "Tell the user something that needs their attention now.",
        "params": [
            {"name": "title", "type": "string", "description": "One line", "required": True},
            {"name": "message", "type": "string", "description": "What happened and why it matters, with facts",
             "required": True},
            {"name": "severity", "type": "string", "description": "info | warning | critical", "required": False},
        ],
    },
    "draft": {
        "description": "Write a document for the user to use themselves: a memo, an email, a plan, a checklist.",
        "params": [
            {"name": "title", "type": "string", "description": "Document title", "required": True},
            {"name": "body", "type": "string", "description": "The document, in markdown", "required": True},
        ],
    },
    "research": {
        "description": "Send the research agent to read the web on a question; what it reads joins the graph.",
        "params": [{"name": "question", "type": "string", "description": "The research question", "required": True}],
    },
}


class ActionError(ValueError):
    pass


# ── Catalog ────────────────────────────────────────────────────────────────

async def catalog(db: ArcadeDB, domain: str, *, with_urls: bool = False) -> list[dict]:
    """Every action the domain can take. Webhook URLs only with `with_urls` (the user's view, never agents')."""
    actions = [{"name": n, "kind": "builtin", "risk": "internal", "confirm": False, **a} for n, a in BUILTIN.items()]
    for a in await kgdb.find_docs(db, domain, "Action", ACTION_JSON, order="name ASC"):
        entry = {"name": a["name"], "kind": "webhook", "risk": "external", "confirm": True,
                 "description": a["description"], "params": a["params"], "dry_run": bool(a.get("dry_run"))}
        if with_urls:
            entry |= {"uid": a["uid"], "url": a["url"]}
        actions.append(entry)
    return actions


async def add_webhook(db: ArcadeDB, domain: str, name: str, description: str, url: str, params: list[dict],
                      dry_run: bool = False) -> dict:
    """A user-configured external action. With `dry_run`, proposing it first POSTs the parameters with
    "dry_run": true, and the endpoint's answer becomes the preview (e.g. a broker's order preview)."""
    if not _NAME.fullmatch(name):
        raise ActionError("name: lowercase letters, digits and _ (2-41 chars), starting with a letter")
    if name in BUILTIN or await kgdb.find_docs(db, domain, "Action", where="name = :name", name=name):
        raise ActionError(f"An action named {name!r} already exists")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ActionError("url must be an http(s) URL")
    for p in params:
        if not _NAME.fullmatch(p.get("name", "")) or p.get("type") not in _TYPES:
            raise ActionError(f"Invalid parameter {p!r}: needs a name and a type ({', '.join(_TYPES)})")
    return await kgdb.insert_doc(db, domain, "Action", {
        "name": name, "description": description.strip(), "url": url.strip(), "dry_run": dry_run,
        "params": [{"name": p["name"], "type": p["type"], "description": p.get("description", ""),
                    "required": bool(p.get("required", True))} for p in params],
    }, ACTION_JSON)


async def remove_webhook(db: ArcadeDB, domain: str, name: str) -> None:
    rows = await kgdb.find_docs(db, domain, "Action", where="name = :name", name=name)
    if not rows:
        raise ActionError(f"No webhook action named {name!r}")
    await kgdb.delete_doc(db, domain, "Action", rows[0]["uid"])


def _check_params(action: dict, params: dict) -> dict:
    spec = {p["name"]: p for p in action["params"]}
    unknown = set(params) - set(spec)
    if unknown:
        raise ActionError(f"Unknown parameters for {action['name']}: {sorted(unknown)}")
    for name, p in spec.items():
        if name not in params:
            if p.get("required", True):
                raise ActionError(f"{action['name']} needs the parameter {name!r} ({p['description']})")
            continue
        value = params[name]
        if not isinstance(value, _TYPES[p["type"]]) or (p["type"] != "boolean" and isinstance(value, bool)):
            raise ActionError(f"{name!r} must be a {p['type']}")
    return params


# ── Proposals ──────────────────────────────────────────────────────────────

async def propose(db: ArcadeDB, domain: str, action: str, params: dict, rationale: str,
                  evidence: list[str] | None = None, source: str = "head", playbook_uid: str | None = None) -> dict:
    """Record a proposed action with its dry-run preview. Internal actions run at once; external ones wait
    for the user. Raises ActionError for unknown actions or bad parameters."""
    actions = {a["name"]: a for a in await catalog(db, domain, with_urls=True)}
    if action not in actions:
        raise ActionError(f"Unknown action {action!r}. Available: {sorted(actions)}")
    spec = actions[action]
    params = _check_params(spec, params or {})
    proposal = await kgdb.insert_doc(db, domain, "Proposal", {
        "action": action, "params": params, "rationale": rationale.strip(), "evidence": evidence or [],
        "source": source, "playbook_uid": playbook_uid, "risk": spec["risk"], "status": "proposed",
        "preview": await _preview(spec, params, domain), "result": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=PROPOSAL_TTL)).isoformat(timespec="seconds"),
    }, PROPOSAL_JSON)
    if not spec["confirm"]:
        return await _execute(db, domain, proposal, spec, decided_by="auto")
    return proposal


async def _preview(spec: dict, params: dict, domain: str) -> str:
    """Dry run: what executing would do, without doing it."""
    if spec["kind"] == "builtin":
        return {"alert": "Show the alert in the Actions tab.", "draft": "Save the draft for you to use.",
                "research": f"Start a research mission: {params.get('question', '')}"}[spec["name"]]
    host = urlsplit(spec["url"]).netloc
    preview = f"POST to {host} ({spec['name']}) with {params}"
    if spec.get("dry_run"):
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                r = await client.post(spec["url"], json={"domain": domain, "action": spec["name"], "params": params,
                                                         "dry_run": True})
            preview += f"\nEndpoint dry run → {r.status_code}: {r.text[:1500]}"
        except httpx.HTTPError as e:
            preview += f"\nEndpoint dry run failed: {type(e).__name__}"
    return preview


async def list_proposals(db: ArcadeDB, domain: str, status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        return await kgdb.find_docs(db, domain, "Proposal", PROPOSAL_JSON, "status = :status", limit=limit,
                                    status=status)
    return await kgdb.find_docs(db, domain, "Proposal", PROPOSAL_JSON, limit=limit)


async def decide(db: ArcadeDB, domain: str, uid: str, approve: bool, note: str = "") -> dict:
    """The user's decision on a proposal: approve → execute exactly as previewed; reject → close it."""
    proposal = await kgdb.get_doc(db, domain, "Proposal", uid, PROPOSAL_JSON)
    if not proposal:
        raise ActionError(f"Proposal {uid} not found")
    if proposal["status"] != "proposed":
        raise ActionError(f"Proposal {uid} is already {proposal['status']}")
    if not approve:
        await kgdb.update_doc(db, domain, "Proposal", uid, {"status": "rejected", "decided_at": now(), "note": note},
                              if_status="proposed")
        return {**proposal, "status": "rejected", "note": note}
    if proposal["expires_at"] < now():
        await kgdb.update_doc(db, domain, "Proposal", uid, {"status": "expired"}, if_status="proposed")
        raise ActionError(f"Proposal {uid} expired: it was based on what was known {PROPOSAL_TTL:g}h ago — "
                          "ask for it again")
    actions = {a["name"]: a for a in await catalog(db, domain, with_urls=True)}
    if proposal["action"] not in actions:
        raise ActionError(f"Action {proposal['action']!r} no longer exists")
    return await _execute(db, domain, proposal, actions[proposal["action"]], decided_by="user", note=note)


async def _execute(db: ArcadeDB, domain: str, proposal: dict, spec: dict, decided_by: str, note: str = "") -> dict:
    # Claim it first: of two confirmations, only one gets past this line.
    if not await kgdb.update_doc(db, domain, "Proposal", proposal["uid"],
                                 {"status": "executing", "decided_at": now(), "decided_by": decided_by,
                                  "note": note}, if_status="proposed"):
        raise ActionError(f"Proposal {proposal['uid']} was already decided")
    params = proposal["params"]
    try:
        if spec["kind"] == "builtin":
            result = await _builtin(spec["name"], params, domain)
        else:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                r = await client.post(spec["url"], json={
                    "domain": domain, "action": spec["name"], "params": params, "dry_run": False,
                    "proposal": proposal["uid"], "rationale": proposal["rationale"],
                })
            result = {"status_code": r.status_code, "response": r.text[:2000]}
            if r.is_error:
                raise ActionError(f"{spec['name']} answered {r.status_code}: {r.text[:300]}")
        status = "executed"
    except (ActionError, httpx.HTTPError) as e:
        result, status = {"error": f"{type(e).__name__}: {e}"}, "failed"
    await kgdb.update_doc(db, domain, "Proposal", proposal["uid"], {"status": status, "executed_at": now(),
                                                                    "result": result}, PROPOSAL_JSON)
    return {**proposal, "status": status, "result": result, "decided_by": decided_by}


async def _builtin(name: str, params: dict, domain: str) -> dict:
    if name in ("alert", "draft"):
        return {"shown": True}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{RESEARCH_URL}/domains/{domain}/research",
                              json={"mode": "mission", "question": params["question"]})
    if r.is_error:
        raise ActionError(f"Research service: {r.status_code} {r.text[:200]}")
    return {"job_id": r.json()["id"]}
