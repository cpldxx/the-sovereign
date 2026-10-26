"""Actions and the MCP guards around them: what the user reads, what a confirm runs, who may confirm, where a webhook
goes. Stand-ins for the database and the network (fast); one db-marked test for expiry on a throwaway database."""

import secrets
import socket
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api import actions as actions_api
from api import mcp
from core import actions, activity, auth
from core import database as kgdb


@pytest.mark.parametrize("text,clean", [
    ("Micron 확인(f_fad13044abd5, weight=1.0), 2028년까지 지속, weight=0.9", "Micron 확인, 2028년까지 지속"),
    ("Capex rises (weight of AI servers in capex).", "Capex rises (weight of AI servers in capex)."),
    ("HBM is tight (uids: f_0123456789ab, f_abcdefabcdef).", "HBM is tight."),
    ("Prices up 12% (evidence=2), see `ep_0123456789ab`.", "Prices up 12%, see."),
    ("Price 3.5 (per share) and weight=0.93.", "Price 3.5 (per share) and."),
    ("A thread thre_0123456789ab and prop_abcdef012345 stay out", "A thread and stay out"),
])
def test_what_the_user_reads_carries_no_internals(text, clean):
    """Uids by their real shape, weight/evidence pairs, and parentheticals that hold nothing else — prose stays."""
    assert actions.scrub(text) == clean


async def test_a_proposal_request_is_repaired_the_same_way_everywhere(monkeypatch):
    async def catalog(db, domain, **kw):
        return [{"name": "alert", "params": [{"name": "title"}, {"name": "message"}]},
                {"name": "vote", "params": [{"name": "reason"}]}]
    monkeypatch.setattr(actions, "catalog", catalog)
    params, why, ev = await actions.tidy_request(None, "d", "alert", {
        "title": "HBM", "message": "Tight (f_0123456789ab)", "rationale": "the graph", "evidence": "f_0123456789ab"},
        "", None)
    assert params == {"title": "HBM", "message": "Tight"} and why == "the graph" and ev == ["f_0123456789ab"]
    params, why, _ = await actions.tidy_request(None, "d", "alert", {"title": "x", "message": "no reason"}, "", None)
    assert why == "no reason"
    # a parameter the action really has stays where it is
    params, why, _ = await actions.tidy_request(None, "d", "vote", {"reason": "mine"}, "why", None)
    assert params == {"reason": "mine"} and why == "why"


def test_a_webhook_revision_changes_with_what_a_confirm_would_run():
    hook = {"kind": "webhook", "url": "https://a.example/x", "auto": False, "dry_run": False, "params": []}
    assert actions.revision(hook) == actions.revision(dict(hook))
    assert actions.revision(hook) != actions.revision({**hook, "url": "https://b.example/x"})
    assert actions.revision(hook) != actions.revision({**hook, "auto": True})
    assert actions.revision({"kind": "builtin", "name": "alert"}) == "builtin"


class Docs:
    """kgdb's document helpers, in memory; `claim` decides what a conditional update returns."""

    def __init__(self, proposal: dict, claim: bool = True):
        self.proposal, self.claim, self.updates = proposal, claim, []

    async def get_doc(self, db, domain, type_, uid, json_fields=()):
        return dict(self.proposal)

    async def update_doc(self, db, domain, type_, uid, changes, json_fields=(), if_status=None):
        self.updates.append(changes)
        return self.claim if if_status else True


def _proposal(**kw) -> dict:
    return {"uid": "prop_0123456789ab", "action": "order", "params": {}, "rationale": "r", "status": "proposed",
            "source": "head", "expires_at": "2999-01-01T00:00:00+00:00", **kw}


async def _hook_catalog(db, domain, **kw):
    return [{"name": "order", "kind": "webhook", "risk": "external", "confirm": True, "params": [],
             "url": "https://shop.example/order", "auto": False, "dry_run": False}]


async def test_a_reject_that_lost_the_race_tells_nobody(monkeypatch):
    docs, told = Docs(_proposal(), claim=False), []
    monkeypatch.setattr(kgdb, "get_doc", docs.get_doc)
    monkeypatch.setattr(kgdb, "update_doc", docs.update_doc)

    async def tell(p, db, domain):
        told.append(p)
    monkeypatch.setattr(actions, "_told", tell)
    with pytest.raises(actions.ActionError, match="already decided"):
        await actions.decide(None, "d", "prop_0123456789ab", approve=False, by="user_1")
    assert told == []


async def test_a_confirm_runs_only_the_api_that_was_proposed(monkeypatch):
    spec = (await _hook_catalog(None, "d"))[0]
    docs = Docs(_proposal(revision=actions.revision({**spec, "url": "https://old.example/order"})))
    monkeypatch.setattr(kgdb, "get_doc", docs.get_doc)
    monkeypatch.setattr(kgdb, "update_doc", docs.update_doc)
    monkeypatch.setattr(actions, "catalog", _hook_catalog)

    async def never(*a, **kw):
        raise AssertionError("executed")
    monkeypatch.setattr(actions, "_execute", never)
    with pytest.raises(actions.ActionError, match="changed since this was proposed"):
        await actions.decide(None, "d", "prop_0123456789ab", approve=True, by="user_1")
    assert docs.updates[-1]["status"] == "expired"


async def test_a_builtin_proposal_from_before_revisions_still_runs(monkeypatch):
    """Proposals stored before revisions carry none: an alert waiting across the upgrade still runs on confirm."""
    docs, ran = Docs(_proposal(action="alert", params={"message": "m"})), []
    monkeypatch.setattr(kgdb, "get_doc", docs.get_doc)
    monkeypatch.setattr(kgdb, "update_doc", docs.update_doc)

    async def builtin_catalog(db, domain, **kw):
        return [{"name": "alert", "kind": "builtin", "risk": "internal", "confirm": False, "params": []}]
    monkeypatch.setattr(actions, "catalog", builtin_catalog)

    async def execute(db, domain, proposal, spec, decided_by, note=""):
        ran.append(proposal["uid"])
        return {**proposal, "status": "executed"}

    async def told(p, db, domain):
        return p
    monkeypatch.setattr(actions, "_execute", execute)
    monkeypatch.setattr(actions, "_told", told)
    assert (await actions.decide(None, "d", "prop_0123456789ab", approve=True, by="user_1"))["status"] == "executed"
    assert ran == ["prop_0123456789ab"]


async def test_a_claimed_proposal_never_stays_executing(monkeypatch):
    docs = Docs(_proposal())
    monkeypatch.setattr(kgdb, "update_doc", docs.update_doc)

    async def broken(name, params, domain):
        raise KeyError("question")
    monkeypatch.setattr(actions, "_builtin", broken)
    out = await actions._execute(None, "d", _proposal(action="research"), {"kind": "builtin", "name": "research"},
                                 decided_by="user_1")
    assert out["status"] == "failed" and "KeyError" in out["result"]["error"]
    assert docs.updates[-1]["status"] == "failed"


async def test_only_a_person_decides_a_proposal(monkeypatch):
    monkeypatch.setattr(actions_api, "_require", lambda d: None)
    seen = {}

    async def decide(db, domain, uid, approve, note="", by="user"):
        seen["by"] = by
        return {"uid": uid, "status": "rejected"}
    monkeypatch.setattr(actions, "decide", decide)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=None)))
    decision = actions_api.Decision(approve=False)
    reset = auth.current.set(auth.Principal("service"))
    try:
        with pytest.raises(HTTPException) as e:
            await actions_api.decide("d", "prop_1", decision, req)
        assert e.value.status_code == 403
    finally:
        auth.current.reset(reset)
    reset = auth.current.set(auth.Principal("user", {"uid": "user_abc"}, {"d": "editor"}))
    try:
        await actions_api.decide("d", "prop_1", decision, req)
    finally:
        auth.current.reset(reset)
    assert seen["by"] == "user_abc"


def _resolver(*answers):
    def getaddrinfo(host, port, *a, **kw):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in answers]
    return getaddrinfo


async def test_a_webhook_goes_to_the_address_that_was_checked(monkeypatch):
    """Resolved once, all public, then connected by that address with the original Host and TLS name (DNS
    rebinding: a second lookup could answer with a private address)."""
    monkeypatch.setattr(actions, "ALLOW_PRIVATE_WEBHOOKS", False)
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34"))
    target, headers, ext = await actions._vetted("https://hooks.example.com:8443/run?x=1")
    assert target == "https://93.184.216.34:8443/run?x=1"
    assert headers == {"Host": "hooks.example.com:8443"} and ext == {"sni_hostname": "hooks.example.com"}
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("93.184.216.34", "10.0.0.5"))
    with pytest.raises(actions.ActionError, match="private address"):
        await actions._vetted("https://hooks.example.com/run")
    monkeypatch.setattr(actions, "ALLOW_PRIVATE_WEBHOOKS", True)
    assert await actions._vetted("http://localhost:5678/hook") == ("http://localhost:5678/hook", {}, {})


@pytest.mark.db
async def test_expired_proposals_are_not_shown_as_waiting(db):
    domain = f"actions_test_{secrets.token_hex(4)}"
    await kgdb.ensure_domain_db(db, domain)
    try:
        old = await kgdb.insert_doc(db, domain, "Proposal", {"action": "alert", "status": "proposed",
                                                             "expires_at": "2000-01-01T00:00:00+00:00"})
        new = await kgdb.insert_doc(db, domain, "Proposal", {"action": "alert", "status": "proposed",
                                                             "expires_at": "2999-01-01T00:00:00+00:00"})
        waiting = await actions.list_proposals(db, domain, "proposed")
        assert [p["uid"] for p in waiting] == [new["uid"]]
        assert (await kgdb.get_doc(db, domain, "Proposal", old["uid"]))["status"] == "expired"
    finally:
        await kgdb.drop_domain_db(db, domain)


def _ctx(principal):
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
        scope={"state": {"principal": principal}})))


async def test_a_tool_call_shows_in_a_feed_only_the_caller_may_use():
    async def peek(domain: str) -> str:
        return "ok"
    tool = mcp._guarded(peek, "viewer")
    d = f"feed_{secrets.token_hex(3)}"
    start = activity.snapshot(d)["seq"]
    await tool(domain=d, ctx=_ctx(auth.Principal("user", {"uid": "u_other"}, {"elsewhere": "owner"})))
    assert activity.snapshot(d, start)["events"] == []
    await tool(domain=d, ctx=_ctx(auth.Principal("user", {"uid": "u_me"}, {d: "viewer"})))
    assert [e["text"] for e in activity.snapshot(d, start)["events"]] == ["peek"]


def test_live_search_needs_a_domain_and_is_paced(monkeypatch):
    monkeypatch.setattr(mcp.registry, "list_domains", lambda: ["d1"])
    monkeypatch.setattr(mcp, "PROXY_CALLS", 3)
    monkeypatch.setattr(mcp, "_proxy_calls", mcp.defaultdict(mcp.deque))
    reset = mcp._caller.set((auth.Principal("user", {"uid": "u_none"}, {}), "viewer"))
    try:
        with pytest.raises(mcp.ToolError, match="need access to a domain"):
            mcp._proxy_allowed()
    finally:
        mcp._caller.reset(reset)
    reset = mcp._caller.set((auth.Principal("user", {"uid": "u_one"}, {"d1": "viewer"}), "viewer"))
    try:
        for _ in range(3):
            mcp._proxy_allowed()
        with pytest.raises(mcp.ToolError, match="Too many"):
            mcp._proxy_allowed()
    finally:
        mcp._caller.reset(reset)
