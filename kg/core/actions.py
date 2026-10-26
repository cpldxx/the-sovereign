"""Actions — the fast path's last step: the Head decides → the Operator runs it → (the user confirms) → the Head hears back.

The catalog of what a domain can do:
    built-in   alert     tell the user something (shown in the Actions tab)            internal: runs at once
               draft     write a document for the user to use (memo, email, plan)      internal: runs at once
               research  send the research agent on a mission                          internal: runs at once
    webhooks   user-configured connectors to an API (a simulation, Slack, n8n, a ticket or lab API, …): a URL that
               receives the action's parameters as JSON and answers with a result. External: waits for the user's
               confirmation — unless the user marked it `auto` (no real-world effect, e.g. a simulation run), then it
               runs when the Head says so.

Only the Head decides to act (propose_action — its Operator runs it); a playbook that fires is reported to the Head,
which decides (core.playbooks). A proposal stores the exact parameters and a dry-run preview of what will happen;
confirming or rejecting one is the user's call through the REST API / UI — no agent tool exists for it. A confirmed
proposal runs exactly as previewed, once, and the Operator reports the result to the Head (core.crew.action_outcome).
Proposals expire after PROPOSAL_TTL hours: a decision made on stale knowledge should be re-proposed, not executed.

Webhooks reach public addresses only — a tenant's webhook must not reach the services beside the KG (Ollama,
ArcadeDB, the other Sovereign APIs). ALLOW_PRIVATE_WEBHOOKS=true lifts that for a single-user install whose
automations run locally (n8n on localhost).
"""

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from core import auth
from core import database as kgdb
from core.database import ArcadeDB, now

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
PROPOSAL_TTL = float(os.getenv("PROPOSAL_TTL_HOURS", "24"))
WEBHOOK_TIMEOUT = 20
ALLOW_PRIVATE_WEBHOOKS = os.getenv("ALLOW_PRIVATE_WEBHOOKS", "false").strip().lower() in ("1", "true", "yes", "on")

ACTION_JSON = ("params",)
PROPOSAL_JSON = ("params", "evidence", "result")
_NAME = re.compile(r"[a-z][a-z0-9_]{1,40}")
_TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool}

BUILTIN = {
    "alert": {
        "description": "Tell the user something that needs their attention now. It pops up in the Sovereign window "
                       "(and as a Mac notification if they turned that on) and stays in Briefing › What was done — "
                       "nothing is sent off this machine.",
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
        entry = {"name": a["name"], "kind": "webhook", "risk": "external", "confirm": not a.get("auto"),
                 "auto": bool(a.get("auto")), "description": a["description"], "params": a["params"],
                 "dry_run": bool(a.get("dry_run"))}
        if with_urls:
            entry |= {"uid": a["uid"], "url": a["url"]}
        actions.append(entry)
    return actions


async def add_webhook(db: ArcadeDB, domain: str, name: str, description: str, url: str, params: list[dict],
                      dry_run: bool = False, auto: bool = False) -> dict:
    """A user-configured external action. With `dry_run`, proposing it first POSTs the parameters with
    "dry_run": true, and the endpoint's answer becomes the preview (e.g. a broker's order preview). With `auto`, it
    runs when the Head says so, without the user's confirmation — for actions with no real-world effect."""
    if not _NAME.fullmatch(name):
        raise ActionError("name: lowercase letters, digits and _ (2-41 chars), starting with a letter")
    if name in BUILTIN or await kgdb.find_docs(db, domain, "Action", where="name = :name", name=name):
        raise ActionError(f"An action named {name!r} already exists")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ActionError("url must be an http(s) URL")
    await _check_destination(url)
    for p in params:
        if not _NAME.fullmatch(p.get("name", "")) or p.get("type") not in _TYPES:
            raise ActionError(f"Invalid parameter {p!r}: needs a name and a type ({', '.join(_TYPES)})")
    return await kgdb.insert_doc(db, domain, "Action", {
        "name": name, "description": description.strip(), "url": url.strip(), "dry_run": dry_run, "auto": auto,
        "params": [{"name": p["name"], "type": p["type"], "description": p.get("description", ""),
                    "required": bool(p.get("required", True))} for p in params],
    }, ACTION_JSON)


async def _check_destination(url: str) -> None:
    """Refuse a webhook URL whose host resolves to a private, loopback or link-local address (checked when it is
    added and again before every call: DNS can change)."""
    await _vetted(url)


async def _vetted(url: str) -> tuple[str, dict, dict]:
    """(address to connect to, headers, request extensions) for a webhook call. The host is resolved once and every
    address must be public; the call then goes to that vetted address with the original Host header and TLS name.
    2026-10-14: checking the name and letting httpx resolve it again was check-then-connect — a DNS answer that
    changes in between (rebinding) reached a private address."""
    parts = urlsplit(url)
    if ALLOW_PRIVATE_WEBHOOKS:
        return url, {}, {}
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or 443,
                                                             type=socket.SOCK_STREAM)
        addrs = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    except (socket.gaierror, UnicodeError, ValueError) as e:
        raise ActionError(f"Can't resolve {parts.hostname}") from e
    if not addrs:
        raise ActionError(f"Can't resolve {parts.hostname}")
    if any(not a.is_global for a in addrs):
        raise ActionError(f"{parts.hostname} is a private address: webhooks go to public endpoints only "
                          "(ALLOW_PRIVATE_WEBHOOKS=true in kg/.env for a single-user install)")
    ip = f"[{addrs[0]}]" if addrs[0].version == 6 else str(addrs[0])
    userinfo, _, hostport = parts.netloc.rpartition("@")
    netloc = (f"{userinfo}@" if userinfo else "") + ip + (f":{parts.port}" if parts.port else "")
    return (urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), {"Host": hostport},
            {"sni_hostname": parts.hostname} if parts.scheme == "https" else {})


async def _post_webhook(url: str, payload: dict) -> httpx.Response:
    """POST to a webhook at its vetted address; redirects are not followed (one could lead anywhere)."""
    target, headers, extensions = await _vetted(url)
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=False) as client:
        return await client.post(target, json=payload, headers=headers, extensions=extensions)


def revision(spec: dict) -> str:
    """What a proposal was made against: a confirm runs only if the action is still the one proposed (a webhook's
    URL, auto or dry-run setting and parameters unchanged)."""
    if spec["kind"] == "builtin":
        return "builtin"
    stamp = json.dumps([spec.get("url"), bool(spec.get("auto")), bool(spec.get("dry_run")), spec.get("params")],
                       sort_keys=True)
    return hashlib.sha256(stamp.encode()).hexdigest()[:16]


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


# ── What the Head writes ───────────────────────────────────────────────────
# Uids and weights a model copied into text a person reads (the same rule as Hermes' answers). Uids have the shape of
# new_uid / insert_doc uids; a parenthetical goes only when it holds nothing but such internals —
# "(weight of AI servers in capex)" is prose.
_UID = r"`?\b[a-z]{1,4}_[0-9a-f]{12}\b`?"
_PAIR = r"\b(?:weight|evidence)\s*[=:]\s*\d+(?:\.\d+)?"
_PAREN = re.compile(r"\s*[(（]([^()（）]*)[)）]")
_LEFT_OVER = re.compile(r"(?:[\s,;/|]|\b(?:uids?|ids?|facts?)\b[:=]?)*", re.I)


def scrub(text: str) -> str:
    def paren(m: re.Match) -> str:
        inner = re.sub(_PAIR, "", re.sub(_UID, "", m[1]), flags=re.I)
        return "" if inner != m[1] and _LEFT_OVER.fullmatch(inner) else m[0]
    text = _PAREN.sub(paren, text)
    text = re.sub(r",?\s*" + _PAIR, "", text, flags=re.I)
    return re.sub(r"\s*" + _UID, "", text)


async def tidy_request(db: ArcadeDB, domain: str, action: str, params: dict | None, rationale: str,
                       evidence: list[str] | None) -> tuple[dict, str, list[str] | None]:
    """A local model's proposal made into what propose() takes: a rationale or evidence tucked into params is taken
    out (measured 2026-10-13: it failed the call 3x and tripped Hermes' circuit breaker), a missing rationale
    defaults to the message, and what the user reads in an alert or a draft carries no uids or weights. Used by
    MCP propose_action and the Operator's run task alike."""
    params = dict(params or {})
    spec = next((a for a in await catalog(db, domain) if a["name"] == action), None)
    own = {p["name"] for p in spec["params"]} if spec else set()
    for key in ("rationale", "reason", "evidence"):
        if key in params and key not in own:
            value = params.pop(key)
            if key == "evidence":
                evidence = evidence or (value if isinstance(value, list) else [str(value)])
            else:
                rationale = rationale or str(value)
    rationale = (rationale or "").strip() or str(params.get("message") or params.get("body") or "(no rationale given)")
    if action in ("alert", "draft"):
        params = {k: scrub(v) if isinstance(v, str) else v for k, v in params.items()}
    return params, rationale, evidence


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
        "preview": await _preview(spec, params, domain), "result": None, "revision": revision(spec),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=PROPOSAL_TTL)).isoformat(timespec="seconds"),
    }, PROPOSAL_JSON)
    if not spec["confirm"]:
        return await _execute(db, domain, proposal, spec, decided_by="auto")
    return proposal


async def _preview(spec: dict, params: dict, domain: str) -> str:
    """Dry run: what executing would do, without doing it."""
    if spec["kind"] == "builtin":
        return {"alert": "Pop the alert up in the Sovereign window and list it in Briefing › What was done.", "draft": "Save the draft for you to use.",
                "research": f"Start a research mission: {params.get('question', '')}"}[spec["name"]]
    host = urlsplit(spec["url"]).netloc
    preview = f"POST to {host} ({spec['name']}) with {params}"
    if spec.get("dry_run"):
        try:
            r = await _post_webhook(spec["url"], {"domain": domain, "action": spec["name"], "params": params,
                                                  "dry_run": True})
            preview += f"\nEndpoint dry run → {r.status_code}: {r.text[:1500]}"
        except (httpx.HTTPError, ActionError) as e:
            preview += f"\nEndpoint dry run failed: {type(e).__name__}: {e}"
    return preview


async def list_proposals(db: ArcadeDB, domain: str, status: str | None = None, limit: int = 50) -> list[dict]:
    # Expiry was applied only when someone confirmed: REST, MCP and the Operator's desk showed stale proposals as
    # still waiting (2026-10-14). Mark them first.
    if await db.exists(domain):
        await kgdb.ensure_domain_db(db, domain, create=False)
        await db.sql(domain, "UPDATE Proposal SET status = 'expired' WHERE status = 'proposed' AND expires_at < :now",
                     now=now())
    if status:
        return await kgdb.find_docs(db, domain, "Proposal", PROPOSAL_JSON, "status = :status", limit=limit,
                                    status=status)
    return await kgdb.find_docs(db, domain, "Proposal", PROPOSAL_JSON, limit=limit)


async def decide(db: ArcadeDB, domain: str, uid: str, approve: bool, note: str = "", by: str = "user") -> dict:
    """The user's decision on a proposal: approve → execute exactly as previewed; reject → close it. `by`: who
    decided (the person's account uid)."""
    proposal = await kgdb.get_doc(db, domain, "Proposal", uid, PROPOSAL_JSON)
    if not proposal:
        raise ActionError(f"Proposal {uid} not found")
    if proposal["status"] != "proposed":
        raise ActionError(f"Proposal {uid} is already {proposal['status']}")
    if not approve:
        if not await kgdb.update_doc(db, domain, "Proposal", uid, {"status": "rejected", "decided_at": now(),
                                                                   "decided_by": by, "note": note},
                                     if_status="proposed"):
            raise ActionError(f"Proposal {uid} was already decided")   # a confirm won the race: no "declined" news
        return await _told({**proposal, "status": "rejected", "note": note, "decided_by": by}, db, domain)
    if proposal["expires_at"] < now():
        await kgdb.update_doc(db, domain, "Proposal", uid, {"status": "expired"}, if_status="proposed")
        raise ActionError(f"Proposal {uid} expired: it was based on what was known {PROPOSAL_TTL:g}h ago — "
                          "ask for it again")
    actions = {a["name"]: a for a in await catalog(db, domain, with_urls=True)}
    if proposal["action"] not in actions:
        raise ActionError(f"Action {proposal['action']!r} no longer exists")
    spec = actions[proposal["action"]]
    # A proposal from before revisions carries none: a built-in can't have changed; a webhook can't be told apart.
    if (proposal.get("revision") or ("builtin" if spec["kind"] == "builtin" else None)) != revision(spec):
        # What the user saw previewed went to another URL / setting: confirming must not run the new one.
        await kgdb.update_doc(db, domain, "Proposal", uid, {"status": "expired", "note": "the API changed"},
                              if_status="proposed")
        raise ActionError(f"The API {proposal['action']!r} changed since this was proposed — propose it again")
    return await _told(await _execute(db, domain, proposal, spec, decided_by=by, note=note), db, domain)


async def _told(proposal: dict, db: ArcadeDB, domain: str) -> dict:
    """The user decided one of the Head's proposals: the Operator tells the Head how it went."""
    from core import crew   # crew imports this module
    try:
        await crew.action_outcome(db, domain, proposal)
    except Exception as e:  # the decision stands even if the Head can't be told
        print(f"[Actions] couldn't tell the Head about {proposal['uid']}: {type(e).__name__}: {e}", flush=True)
    return proposal


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
            r = await _post_webhook(spec["url"], {
                "domain": domain, "action": spec["name"], "params": params, "dry_run": False,
                "proposal": proposal["uid"], "rationale": proposal["rationale"]})
            result = {"status_code": r.status_code, "response": r.text[:2000]}
            if r.is_error:
                raise ActionError(f"{spec['name']} answered {r.status_code}: {r.text[:300]}")
        status = "executed"
    except asyncio.CancelledError:
        await kgdb.update_doc(db, domain, "Proposal", proposal["uid"], {
            "status": "failed", "executed_at": now(), "result": {"error": "interrupted (the KG stopped)"}},
            PROPOSAL_JSON)
        raise
    except Exception as e:   # claimed: whatever happens, it ends executed or failed — never stuck "executing"
        result, status = {"error": f"{type(e).__name__}: {e}"[:2000]}, "failed"
    await kgdb.update_doc(db, domain, "Proposal", proposal["uid"], {"status": status, "executed_at": now(),
                                                                    "result": result}, PROPOSAL_JSON)
    return {**proposal, "status": status, "result": result, "decided_by": decided_by}


async def _builtin(name: str, params: dict, domain: str) -> dict:
    if name in ("alert", "draft"):
        return {"shown": True}
    async with httpx.AsyncClient(timeout=30, headers=auth.headers(domain)) as client:
        r = await client.post(f"{RESEARCH_URL}/domains/{domain}/research",
                              json={"mode": "mission", "question": params["question"]})
    if r.is_error:
        raise ActionError(f"Research service: {r.status_code} {r.text[:200]}")
    return {"job_id": r.json()["id"]}
