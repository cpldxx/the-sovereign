"""Security invariants: what must never happen, checked against the real pieces.

    webhooks   never reach this machine or a private network                         (offline)
    accounts   sign-up gates, throttling, revocation — in a throwaway accounts DB      (db)
    sandbox    sensor code can't reach the host, can't run forbidden code, can't leak secrets, can't skip
               robots.txt                                                             (sandbox; robots also slow)
    services   nothing answers without credentials; a domain-scoped connection can't leave its domain  (live)
"""

import json
import secrets

import httpx
import pytest

from core import accounts, actions, auth, sensors
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


# ── Webhooks ─────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["http://localhost:11434/api/tags", "http://127.0.0.1:2480/", "http://[::1]:8080/",
                                 "http://169.254.169.254/latest/meta-data", "http://10.0.0.1/", "http://192.168.1.1/"])
async def test_webhooks_never_reach_private_addresses(url, monkeypatch):
    monkeypatch.setattr(actions, "ALLOW_PRIVATE_WEBHOOKS", False)
    with pytest.raises(actions.ActionError):
        await actions._check_destination(url)


# ── Accounts (a throwaway accounts database) ─────────────────────────────────────────────────────────────────────

@pytest.fixture
async def store(db, monkeypatch):
    name = f"sovereign_test_{secrets.token_hex(3)}"
    monkeypatch.setattr(accounts, "SYSTEM_DB", name)
    monkeypatch.setattr(accounts, "SIGNUP", "invite")
    for cache in ("_principals", "_failures"):
        monkeypatch.setattr(accounts, cache, {} if cache == "_principals" else accounts.defaultdict(list))
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


# ── Sandbox ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _module(body: str) -> str:
    return ('import httpx\nNAME = "test_probe"\nDESCRIPTION = "x"\n'
            'PARAMS = {"url": {"type": "string", "description": "u", "example": "x"}}\n'
            f'def run(url: str) -> dict:\n{body}\n')


@pytest.mark.sandbox
@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/health", "http://host.docker.internal:11434/api/tags",
                                 "http://localhost:2480/", "http://169.254.169.254/"])
async def test_sandbox_cannot_reach_this_machine(url):
    out = await sensors.run_code(_module("    return {'status': httpx.get(url, timeout=5).status_code}"), {"url": url})
    assert not out["ok"], out


@pytest.mark.sandbox
async def test_sandbox_refuses_forbidden_code_without_running_it():
    out = await sensors.run_code(_module("    import os\n    return {'x': os.listdir('/')}"), {"url": "x"})
    assert not out["ok"] and "not allowed" in out["error"]


@pytest.mark.sandbox
async def test_sandbox_masks_secrets(monkeypatch):
    monkeypatch.setenv("SENSOR_SECRETS", "TEST_SECRET_KEY")
    monkeypatch.setenv("TEST_SECRET_KEY", "s3cr3t-value-123")
    code = _module("    return {'leak': secret('TEST_SECRET_KEY')}").replace(
        'PARAMS =', 'SECRETS = ["TEST_SECRET_KEY"]\nPARAMS =')
    out = await sensors.run_code(code, {"url": "x"})
    assert "s3cr3t-value-123" not in json.dumps(out)


@pytest.mark.sandbox
@pytest.mark.slow
async def test_sandbox_enforces_robots_txt():
    out = await sensors.probe("https://query1.finance.yahoo.com/v8/finance/chart/NVDA")   # disallowed for bots
    assert not out["ok"] and "robots.txt" in out["error"]


# ── Running services ─────────────────────────────────────────────────────────────────────────────────────────────

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
