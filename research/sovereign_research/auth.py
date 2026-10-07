"""Who is calling, and what they may touch — the same file in kg/core, hermes/ and research/sovereign_research.

Principals
    user     a person: a browser session (httpOnly cookie `sovereign_session`, set by the KG's /auth/login) or an API
             token (Authorization: Bearer svk_…, for MCP clients and scripts). Reaches only the domains they are a
             member of, with their role there.
    service  the Sovereign services themselves (Bearer SOVEREIGN_TOKEN, which never reaches a browser): every
             domain — or only one, when the call carries X-Sovereign-Domain (Hermes' per-domain Head connections,
             so no prompt can talk a Head into another tenant's graph).

Roles per domain: viewer < editor < owner. What a request needs follows from its path and method (`needed`): reads
need viewer, writes editor; sharing, webhooks and deleting the domain need owner. A domain the caller has no role in
answers 404, as if it didn't exist.

The KG resolves tokens against its account store (`use(...)`). Hermes and Research ask the KG (/auth/me) and cache
the answer for CACHE_SECONDS — a revoked session or membership stops working there within that time.
"""

import contextvars
import hashlib
import hmac
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

import httpx
from dotenv import load_dotenv
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse

load_dotenv()
TOKEN = os.getenv("SOVEREIGN_TOKEN", "").strip()
KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
# Browser origins allowed to call the APIs with a session cookie (comma-separated).
ORIGINS = [o.strip() for o in os.getenv("SOVEREIGN_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
COOKIE = "sovereign_session"
PUBLIC = ("/health", "/auth/config", "/auth/login", "/auth/signup")
CACHE_SECONDS = 15
ROLES = {"viewer": 1, "editor": 2, "owner": 3}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass(frozen=True)
class Principal:
    kind: str                                    # "user" | "service"
    user: dict | None = None                     # users: {uid, email, name, admin}
    roles: dict = field(default_factory=dict)    # users: {domain: role}
    scope: str | None = None                     # service: limited to this domain
    via: str = "bearer"                          # "bearer" | "cookie"

    def role(self, domain: str) -> str | None:
        if self.kind == "service":
            return "owner" if self.scope in (None, domain) else None
        return self.roles.get(domain)

    def can(self, domain: str, need: str = "viewer") -> bool:
        return ROLES.get(self.role(domain) or "", 0) >= ROLES[need]

    def visible(self, domains: list[str]) -> list[str]:
        return [d for d in domains if self.role(d)]


# The caller of the request being handled (REST handlers, and threads they start; MCP tools get it from their
# request instead — they run in the MCP session's task).
current: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("principal", default=None)


def headers(domain: str | None = None) -> dict:
    """Headers for calls to the other Sovereign services — as the service, limited to `domain` if given."""
    return {"Authorization": f"Bearer {TOKEN}", **({"X-Sovereign-Domain": domain} if domain else {})}


# ── What a request needs ───────────────────────────────────────────────────

_DOMAIN_PATH = re.compile(r"^/(?:domains|graph)/(?P<domain>[^/]+)(?P<rest>/.*)?$")
_READ_POSTS = re.compile(r"^/(?:query|ask|ask/stream|sensors/[^/]+/read)$")   # POSTs that only read
_ANY_MEMBER = (("DELETE", re.compile(r"^/members/[^/]+$")),)                 # leaving (the handler checks whose)
_OWNER = (("DELETE", re.compile(r"^$")), ("PUT", re.compile(r"^/members$")),
          ("POST", re.compile(r"^/actions$")), ("DELETE", re.compile(r"^/actions/[^/]+$")))


def needed(method: str, path: str) -> tuple[str, str] | None:
    """(domain, role) a request needs, for paths under /domains/{domain} and /graph/{domain}."""
    if not (m := _DOMAIN_PATH.match(path)):
        return None
    domain, rest = m["domain"], (m["rest"] or "").rstrip("/")
    if method in SAFE_METHODS or (method == "POST" and _READ_POSTS.match(rest)):
        return domain, "viewer"
    if any(method == verb and pattern.match(rest) for verb, pattern in _ANY_MEMBER):
        return domain, "viewer"
    if any(method == verb and pattern.match(rest) for verb, pattern in _OWNER):
        return domain, "owner"
    return domain, "editor"


def denial(principal: Principal | None, domain: str, need: str = "viewer") -> tuple[int, str] | None:
    """Why `principal` may not do this to `domain` — (status, message) — or None if it may."""
    role = principal.role(domain) if principal else None
    if not role:
        return 404, f"Domain '{domain}' does not exist"
    if ROLES[role] < ROLES[need]:
        return 403, f"This needs {need} access to '{domain}' (you are {role})"
    return None


def require(domain: str, need: str = "viewer") -> Principal:
    """In a REST handler: the caller, if they may do this to `domain`; HTTP 404/403 otherwise."""
    from fastapi import HTTPException  # noqa: PLC0415

    principal = current.get()
    if problem := denial(principal, domain, need):
        raise HTTPException(status_code=problem[0], detail=problem[1])
    return principal


# ── Resolving a token ──────────────────────────────────────────────────────

Authenticator = Callable[[str, bool], Awaitable[Principal | None]]
_cache: dict[str, tuple[Principal | None, float]] = {}


async def _ask_kg(token: str, fresh: bool = False) -> Principal | None:
    """A user's principal from the KG (the account store), cached briefly."""
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[1] < CACHE_SECONDS:
        return hit[0]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{KG_URL}/auth/me", headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        principal = None
    else:
        r.raise_for_status()
        body = r.json()
        principal = Principal("user", body["user"], body["roles"]) if body.get("kind") == "user" else None
    if len(_cache) > 10_000:
        _cache.clear()
    _cache[key] = (principal, time.monotonic())
    return principal


_authenticator: Authenticator = _ask_kg


def use(authenticator: Authenticator) -> None:
    """Resolve user tokens with this instead of asking the KG (the KG itself, which holds the accounts)."""
    global _authenticator
    _authenticator = authenticator


def credentials(headers_: Headers) -> tuple[str, str] | None:
    """(token, "bearer" | "cookie") from a request's headers."""
    given = headers_.get("authorization", "")
    if given[:7].lower() == "bearer " and given[7:].strip():
        return given[7:].strip(), "bearer"
    if token := cookie_parser(headers_.get("cookie", "")).get(COOKIE):
        return token, "cookie"
    return None


async def resolve(token: str, via: str, headers_: Headers, fresh: bool = False) -> Principal | None:
    if via == "bearer" and TOKEN and hmac.compare_digest(token.encode(), TOKEN.encode()):
        return Principal("service", scope=headers_.get("x-sovereign-domain") or None)
    principal = await _authenticator(token, fresh)
    return replace(principal, via=via) if principal else None


class Auth:
    """Pure ASGI middleware (safe for streaming responses: SSE, MCP): resolves the caller, enforces what the path
    needs, and leaves the principal in `request.state.principal` and `current`."""

    def __init__(self, app):
        if not TOKEN:
            raise RuntimeError("SOVEREIGN_TOKEN is not set. Put the same random value in kg/.env, hermes/.env and "
                               "research/.env (python -c 'import secrets; print(secrets.token_urlsafe(32))').")
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or scope["path"] in PUBLIC:
            await self.app(scope, receive, send)
            return

        async def deny(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        headers_ = Headers(scope=scope)
        if not (found := credentials(headers_)):
            return await deny(401, "Sign in first")
        token, via = found
        try:
            principal = await resolve(token, via, headers_)
        except httpx.HTTPError:
            return await deny(503, "Can't check the session: the KG API is unreachable")
        if principal is None:
            return await deny(401, "Session expired or invalid — sign in again")
        # A session cookie rides along on any request the browser makes: writes must come from our own pages.
        if via == "cookie" and scope["method"] not in SAFE_METHODS:
            origin = headers_.get("origin")
            if origin and origin not in ORIGINS:
                return await deny(403, "Cross-site request refused")
        if need := needed(scope["method"], scope["path"]):
            domain, role = need
            if principal.kind == "user" and not principal.can(domain, role):
                principal = await resolve(token, via, headers_, fresh=True) or principal  # a domain made just now
            if problem := denial(principal, domain, role):
                return await deny(*problem)
        scope.setdefault("state", {})["principal"] = principal
        reset = current.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            current.reset(reset)
