"""Security invariants: what must never happen, checked against the real pieces.

    accounts   sign-up gates, throttling, revocation — in a throwaway accounts DB      (db)
    selfcheck  the nightly check flags a crawler that would read what robots.txt forbids  (offline)
    services   nothing answers without credentials; a domain-scoped connection can't leave its domain  (live)
"""

import json
import secrets

import httpx
import pytest

from core import accounts, auth, selfcheck
from core.auth import Principal

from conftest import SERVICE, URLS


# ── MCP host guard ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,ok", [("kg:8080", True), ("localhost", True), ("localhost:8000", True),
                                     ("127.0.0.1:8080", True), ("evil.example", False), ("kg.evil.example:80", False)])
def test_mcp_answers_only_known_hosts(host, ok):
    """Hermes reaches the MCP as kg:8080 inside compose, the UI through nginx as localhost; nothing else (DNS rebinding)."""
    from mcp.server.transport_security import TransportSecurityMiddleware
    import main
    assert TransportSecurityMiddleware(main.MCP_SECURITY)._validate_host(host) is ok


# ── Accounts (a throwaway accounts database) ─────────────────────────────────────────────────────────────────────

@pytest.fixture
async def store(db, monkeypatch):
    name = f"sovereign_test_{secrets.token_hex(3)}"
    monkeypatch.setattr(accounts, "SYSTEM_DB", name)
    monkeypatch.setattr(accounts, "SIGNUP", "invite")
    for cache in ("_principals", "_failures"):
        monkeypatch.setattr(accounts, cache, {})
    before = auth._authenticator
    await accounts.setup(db)
    yield accounts
    auth.use(before)
    await db.server(f"drop database {name}")


@pytest.mark.db
async def test_accounts_lifecycle(store):
    pw = secrets.token_urlsafe(12)
    admin, admin_token = await store.signup("first@example.test", pw)
    assert admin["admin"]                                                     # the first account is the admin
    with pytest.raises(accounts.AccountError):                                 # then: invite only
        await store.signup("second@example.test", secrets.token_urlsafe(12))
    invite = await store.create_invite(admin["uid"])
    user, user_token = await store.signup("second@example.test", secrets.token_urlsafe(12), invite=invite["code"])
    assert not user["admin"]
    with pytest.raises(accounts.AccountError):                                 # single use
        await store.signup("third@example.test", secrets.token_urlsafe(12), invite=invite["code"])
    with pytest.raises(accounts.AccountError):                                 # no short passwords
        await store.signup("x@example.test", "short", invite=(await store.create_invite(admin["uid"]))["code"])

    assert (await store.principal(user_token)).user["email"] == "second@example.test"
    await store.revoke(user_token)
    assert await store.principal(user_token) is None                           # signed out = gone

    other_session = (await store.login("first@example.test", pw))[1]
    await store.change_password(admin["uid"], pw, secrets.token_urlsafe(12), keep_token=admin_token)
    assert await store.principal(other_session, fresh=True) is None            # other sessions end
    assert await store.principal(admin_token, fresh=True) is not None

    for _ in range(store.MAX_FAILURES["email"]):
        with pytest.raises(accounts.AccountError):
            await store.login("first@example.test", "wrong-password", client="t")
    with pytest.raises(accounts.AccountError) as throttled:
        await store.login("first@example.test", pw, client="t")
    assert throttled.value.status == 429                                       # throttled even with the right one


@pytest.mark.db
async def test_tokens_and_sharing(store):
    admin, _ = await store.signup("owner@example.test", secrets.token_urlsafe(12))
    token = await store.create_api_token(admin["uid"], "test client")
    assert (await store.principal(token["token"])).user["uid"] == admin["uid"]
    await store.revoke_api_token(admin["uid"], token["uid"])
    assert await store.principal(token["token"], fresh=True) is None
    domain = (await store.roles_of(admin["uid"])) and next(iter(await store.roles_of(admin["uid"])))
    if domain:                                                                 # the first account owns them all
        with pytest.raises(accounts.AccountError):                             # the last owner can't leave
            await store.remove_member(domain, admin["uid"])


# ── Self-check ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_selfcheck_flags_a_crawler_that_ignores_robots_txt(monkeypatch):
    """The nightly check asks the research service about a URL robots.txt disallows: allowed is a failure."""
    class Answer:
        def __init__(self, body):
            self.body = body
        def raise_for_status(self):
            return None
        def json(self):
            return self.body

    class Client:
        def __init__(self, body):
            self.body = body
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def get(self, *a, **kw):
            return Answer(self.body)

    for body, status in (({"allowed": True, "reason": ""}, "fail"),
                         ({"allowed": False, "reason": "robots.txt disallows it"}, "pass")):
        monkeypatch.setattr(selfcheck.httpx, "AsyncClient", lambda *a, _b=body, **kw: Client(_b))
        assert (await selfcheck._robots())[0] == status


@pytest.mark.live
@pytest.mark.parametrize("service,method,path", [
    ("kg", "GET", "/domains"), ("kg", "POST", "/mcp"), ("kg", "GET", "/auth/me"), ("kg", "GET", "/docs"),
    ("hermes", "POST", "/voice/warm"), ("hermes", "POST", "/voice/transcribe"), ("research", "GET", "/jobs"),
    ("research", "POST", "/fetch"),
])
def test_nothing_answers_without_credentials(service, method, path):
    assert httpx.request(method, URLS[service] + path, timeout=10).status_code == 401


@pytest.mark.live
def test_health_stays_public():
    assert all(httpx.get(f"{u}/health", timeout=10).status_code == 200 for u in URLS.values())


@pytest.mark.live
def test_a_scoped_connection_cannot_leave_its_domain():
    domains = [d["id"] for d in httpx.get(f"{URLS['kg']}/domains", headers=auth.headers(), timeout=10)
               .json()["domains"]]
    if len(domains) < 2:
        pytest.skip("needs two domains")
    a, b = domains[:2]
    scoped = auth.headers(a)
    assert httpx.get(f"{URLS['kg']}/domains/{a}", headers=scoped, timeout=10).status_code == 200
    assert httpx.get(f"{URLS['kg']}/domains/{b}", headers=scoped, timeout=10).status_code == 404
    listed = httpx.get(f"{URLS['kg']}/domains", headers=scoped, timeout=10).json()["domains"]
    assert [d["id"] for d in listed] == [a]


def test_service_token_is_set_and_strong():
    assert SERVICE and len(SERVICE) >= 32, "SOVEREIGN_TOKEN must be set to a long random value"
    assert Principal("service", scope="a").visible(["a", "b"]) == ["a"]
