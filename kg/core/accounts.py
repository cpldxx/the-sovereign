"""Accounts — people, their sessions and API tokens, sign-up invites, and who may use which domain.

Kept in their own ArcadeDB database (SYSTEM_DB), apart from every domain's graph:
    Account  uid, email (unique, lowercase), name, password (scrypt), admin, created_at
    Token    uid, token_hash (unique — the token itself is never stored), user_uid, kind ("browser" | "api"), label,
             created_at, expires_at (null: until revoked)
    Invite   uid, code_hash, created_by, created_at, expires_at, used_by — permission to create one account
    Member   domain, user_uid, role ("owner" | "editor" | "viewer"), added_by, created_at

Sign-up (SIGNUP): "invite" (default) — an invite code from an admin; "open" — anyone. The very first account needs
neither: it becomes the admin and the owner of every domain that existed before accounts did.
Sharing adds an existing account to a domain by email (no email is ever sent; invites are links you pass on).
Trusted domains — owned by an admin — may use the operator's resources: the OpenHands Coder (it drives Docker) and
the API keys in SENSOR_SECRETS. Everyone else's sensors are written by the built-in Coder and run without them.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from core import auth
from core.auth import Principal
from core.database import ArcadeDB, ArcadeDBError, new_uid, now
from domains import registry

SYSTEM_DB = registry.SYSTEM_DB
SIGNUP = os.getenv("SIGNUP", "invite").strip().lower()          # "invite" | "open"
SESSION_DAYS = float(os.getenv("SESSION_DAYS", "30"))
INVITE_DAYS = 7
CACHE_SECONDS = 30
MAX_FAILURES = {"email": 10, "client": 50}   # failed sign-ins per account / per address (higher: one address can
FAILURE_WINDOW = 15 * 60                      # be a whole office or a proxy) within this many seconds, then 429
ROLES = tuple(auth.ROLES)

_SCHEMA = """
CREATE DOCUMENT TYPE Account IF NOT EXISTS;
CREATE PROPERTY Account.uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Account (uid) UNIQUE;
CREATE PROPERTY Account.email IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Account (email) UNIQUE;
CREATE DOCUMENT TYPE Token IF NOT EXISTS;
CREATE PROPERTY Token.token_hash IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Token (token_hash) UNIQUE;
CREATE PROPERTY Token.user_uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Token (user_uid) NOTUNIQUE;
CREATE DOCUMENT TYPE Invite IF NOT EXISTS;
CREATE PROPERTY Invite.code_hash IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Invite (code_hash) UNIQUE;
CREATE DOCUMENT TYPE Member IF NOT EXISTS;
CREATE PROPERTY Member.domain IF NOT EXISTS STRING;
CREATE PROPERTY Member.user_uid IF NOT EXISTS STRING;
CREATE INDEX IF NOT EXISTS ON Member (domain, user_uid) UNIQUE;
CREATE INDEX IF NOT EXISTS ON Member (user_uid) NOTUNIQUE;
"""

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class AccountError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── Store ──────────────────────────────────────────────────────────────────

_db: ArcadeDB | None = None
_signup_lock = asyncio.Lock()
_principals: dict[str, tuple[Principal | None, float]] = {}   # token hash → principal
_trusted: set[str] = set()                                    # domains owned by an admin


async def setup(db: ArcadeDB) -> None:
    """Create the account store if missing, and resolve user tokens against it from now on."""
    global _db
    _db = db
    if not await db.exists(SYSTEM_DB):
        await db.server(f"create database {SYSTEM_DB}")
    await db.command(SYSTEM_DB, "sqlscript", _SCHEMA)
    await _load_trusted()
    auth.use(principal)


async def _sql(query: str, **params) -> list[dict]:
    return await _db.sql(SYSTEM_DB, query, **params)


async def _insert(type_: str, row: dict) -> None:
    # Names quoted and parameters prefixed: some are SQL keywords (role).
    await _sql(f"INSERT INTO {type_} SET " + ", ".join(f"`{k}` = :p_{k}" for k in row),
               **{f"p_{k}": v for k, v in row.items()})


def _clean(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("@")}


def _public(account: dict) -> dict:
    return {"uid": account["uid"], "email": account["email"], "name": account.get("name") or "",
            "admin": bool(account.get("admin"))}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _forget(user_uid: str | None = None) -> None:
    """Drop cached principals (all, or one user's) after their sessions, roles or account changed."""
    global _principals
    _principals = {} if user_uid is None else {
        k: v for k, v in _principals.items() if not (v[0] and v[0].user["uid"] == user_uid)}


# ── Passwords ──────────────────────────────────────────────────────────────

_N, _R, _P = 2**15, 8, 1


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, _N, _R, _P)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        return hmac.compare_digest(_scrypt(password, base64.b64decode(salt), int(n), int(r), int(p)),
                                   base64.b64decode(digest))
    except ValueError:
        return False


_DUMMY = hash_password(secrets.token_urlsafe(16))   # checked against for unknown emails: same timing as a real one


def _check_password(password: str, email: str = "") -> None:
    if len(password) < 10:
        raise AccountError("Use a password of at least 10 characters")
    if len(password) > 256:
        raise AccountError("That password is too long")
    if email and password.lower() == email.lower():
        raise AccountError("Don't use your email as the password")


def _check_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise AccountError("Enter a valid email address")
    return email


# ── Sessions and tokens ────────────────────────────────────────────────────

async def _issue(user_uid: str, kind: str, label: str = "") -> tuple[str, dict]:
    token = ("svs_" if kind == "browser" else "svk_") + secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds") \
        if kind == "browser" else None
    row = {"uid": new_uid("tok"), "token_hash": _hash(token), "user_uid": user_uid, "kind": kind,
           "label": label.strip()[:80], "created_at": now(), "expires_at": expires}
    await _insert("Token", row)
    return token, row


async def principal(token: str, fresh: bool = False) -> Principal | None:
    """The account behind a session or API token, with its roles — or None (unknown, expired or revoked)."""
    key = _hash(token)
    hit = _principals.get(key)
    if hit and not fresh and time.monotonic() - hit[1] < CACHE_SECONDS:
        found = hit[0]
    else:
        found = None
        rows = await _sql("SELECT FROM Token WHERE token_hash = :h", h=key)
        if rows and (rows[0].get("expires_at") is None or rows[0]["expires_at"] > now()):
            accounts = await _sql("SELECT FROM Account WHERE uid = :uid", uid=rows[0]["user_uid"])
            if accounts:
                found = Principal("user", _public(accounts[0]), await roles_of(accounts[0]["uid"]))
        if len(_principals) > 10_000:
            _principals.clear()
        _principals[key] = (found, time.monotonic())
    return found


async def revoke(token: str) -> None:
    rows = await _sql("SELECT user_uid FROM Token WHERE token_hash = :h", h=_hash(token))
    await _sql("DELETE FROM Token WHERE token_hash = :h", h=_hash(token))
    if rows:
        _forget(rows[0]["user_uid"])


async def api_tokens(user_uid: str) -> list[dict]:
    rows = await _sql("SELECT uid, label, created_at FROM Token WHERE user_uid = :u AND kind = 'api' "
                      "ORDER BY created_at DESC", u=user_uid)
    return [_clean(r) for r in rows]


async def create_api_token(user_uid: str, label: str) -> dict:
    if not label.strip():
        raise AccountError("Give the token a name (what will use it)")
    token, row = await _issue(user_uid, "api", label)
    return {"uid": row["uid"], "label": row["label"], "created_at": row["created_at"], "token": token}


async def revoke_api_token(user_uid: str, uid: str) -> None:
    rows = await _sql("DELETE FROM Token WHERE uid = :uid AND user_uid = :u AND kind = 'api'", uid=uid, u=user_uid)
    if not (rows and rows[0].get("count")):
        raise AccountError(f"No API token {uid}", 404)
    _forget(user_uid)


# ── Sign-up and sign-in ────────────────────────────────────────────────────

async def has_accounts() -> bool:
    return bool(await _sql("SELECT uid FROM Account LIMIT 1"))


async def signup(email: str, password: str, name: str = "", invite: str = "") -> tuple[dict, str]:
    """A new account and its first browser session → (account, session token)."""
    email = _check_email(email)
    _check_password(password, email)
    stored = await asyncio.to_thread(hash_password, password)
    async with _signup_lock:   # one at a time: exactly one account can be the first
        first = not await has_accounts()
        invite_uid = None
        if not first and SIGNUP != "open":
            invite_uid = await _valid_invite(invite)
        account = {"uid": new_uid("user"), "email": email, "name": name.strip()[:80], "password": stored,
                   "admin": first, "created_at": now()}
        try:
            await _insert("Account", account)
        except ArcadeDBError as e:
            if "duplicate" in str(e).lower():
                raise AccountError("An account with this email already exists — sign in instead", 409) from e
            raise
        if invite_uid:
            await _sql("UPDATE Invite SET used_by = :u WHERE uid = :uid", u=account["uid"], uid=invite_uid)
    if first:
        await _claim_unowned(account["uid"])
        await _load_trusted()
    token, _ = await _issue(account["uid"], "browser")
    return _public(account), token


async def _claim_unowned(user_uid: str) -> None:
    """Domains from before accounts existed belong to the first account."""
    owned = {r["domain"] for r in await _sql("SELECT domain FROM Member")}
    for domain in registry.list_domains():
        if domain not in owned:
            await add_member(domain, user_uid, "owner", added_by=user_uid)


_failures: dict[str, list[float]] = defaultdict(list)


def _throttled(*keys: str) -> bool:
    cutoff = time.monotonic() - FAILURE_WINDOW
    for k in keys:
        _failures[k] = [t for t in _failures[k] if t > cutoff]
    return any(len(_failures[k]) >= MAX_FAILURES[k.split(":", 1)[0]] for k in keys)


async def login(email: str, password: str, client: str = "") -> tuple[dict, str]:
    """Check the password → (account, new session token). Repeated failures for an email or an address → 429."""
    email = email.strip().lower()
    keys = (f"email:{email}", f"client:{client}")
    if _throttled(*keys):
        raise AccountError("Too many failed sign-ins — wait a few minutes", 429)
    rows = await _sql("SELECT FROM Account WHERE email = :e", e=email)
    ok = await asyncio.to_thread(verify_password, password, rows[0]["password"] if rows else _DUMMY)
    if not (rows and ok):
        for k in keys:
            _failures[k].append(time.monotonic())
        raise AccountError("Wrong email or password", 401)
    _failures.pop(keys[0], None)
    token, _ = await _issue(rows[0]["uid"], "browser")
    return _public(rows[0]), token


async def change_password(user_uid: str, current: str, new: str, keep_token: str) -> None:
    """New password; every other browser session is signed out (API tokens stay)."""
    rows = await _sql("SELECT FROM Account WHERE uid = :uid", uid=user_uid)
    if not rows or not await asyncio.to_thread(verify_password, current, rows[0]["password"]):
        raise AccountError("The current password is wrong", 403)
    _check_password(new, rows[0]["email"])
    stored = await asyncio.to_thread(hash_password, new)
    await _sql("UPDATE Account SET `password` = :p_password WHERE uid = :uid", p_password=stored, uid=user_uid)
    await _sql("DELETE FROM Token WHERE user_uid = :u AND kind = 'browser' AND token_hash <> :h",
               u=user_uid, h=_hash(keep_token))
    _forget(user_uid)


# ── Invites (admins) ───────────────────────────────────────────────────────

async def create_invite(created_by: str) -> dict:
    code = secrets.token_urlsafe(18)
    row = {"uid": new_uid("inv"), "code_hash": _hash(code), "created_by": created_by, "created_at": now(),
           "expires_at": (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds"),
           "used_by": None}
    await _insert("Invite", row)
    return {"uid": row["uid"], "code": code, "expires_at": row["expires_at"]}


async def list_invites() -> list[dict]:
    rows = await _sql("SELECT uid, created_at, expires_at, used_by FROM Invite ORDER BY created_at DESC LIMIT 50")
    return [_clean(r) for r in rows]


async def revoke_invite(uid: str) -> None:
    await _sql("DELETE FROM Invite WHERE uid = :uid AND used_by IS NULL", uid=uid)


async def _valid_invite(code: str) -> str:
    rows = await _sql("SELECT FROM Invite WHERE code_hash = :h", h=_hash(code.strip())) if code.strip() else []
    if not rows or rows[0].get("used_by") or rows[0]["expires_at"] < now():
        raise AccountError("Sign-up needs a valid invite link — ask the admin for one", 403)
    return rows[0]["uid"]


# ── Members ────────────────────────────────────────────────────────────────

async def roles_of(user_uid: str) -> dict[str, str]:
    rows = await _sql("SELECT domain, `role` FROM Member WHERE user_uid = :u", u=user_uid)
    return {r["domain"]: r["role"] for r in rows}


async def members(domain: str) -> list[dict]:
    rows = await _sql("SELECT user_uid, `role`, created_at FROM Member WHERE domain = :d", d=domain)
    if not rows:
        return []
    accounts = {a["uid"]: a for a in await _sql("SELECT uid, email, name FROM Account WHERE uid IN :uids",
                                                uids=[r["user_uid"] for r in rows])}
    order = {role: i for i, role in enumerate(reversed(ROLES))}
    return sorted(({"email": accounts[r["user_uid"]]["email"], "name": accounts[r["user_uid"]].get("name") or "",
                    "uid": r["user_uid"], "role": r["role"], "since": r["created_at"]}
                   for r in rows if r["user_uid"] in accounts), key=lambda m: (order[m["role"]], m["email"]))


async def add_member(domain: str, user_uid: str, role: str, added_by: str) -> None:
    if role not in ROLES:
        raise AccountError(f"role must be one of {', '.join(ROLES)}")
    updated = await _sql("UPDATE Member SET `role` = :p_role WHERE domain = :d AND user_uid = :u", p_role=role,
                         d=domain, u=user_uid)
    if not (updated and updated[0].get("count")):
        await _insert("Member", {"domain": domain, "user_uid": user_uid, "role": role, "added_by": added_by,
                                 "created_at": now()})
    _forget(user_uid)
    await _load_trusted()


async def share(domain: str, email: str, role: str, by: str) -> list[dict]:
    """Give an existing account a role in the domain (or change it). The last owner can't be demoted."""
    rows = await _sql("SELECT uid FROM Account WHERE email = :e", e=email.strip().lower())
    if not rows:
        raise AccountError(f"No account for {email.strip()} — they need to sign up first", 404)
    if role != "owner":
        await _keep_an_owner(domain, rows[0]["uid"])
    await add_member(domain, rows[0]["uid"], role, added_by=by)
    return await members(domain)


async def remove_member(domain: str, user_uid: str) -> None:
    await _keep_an_owner(domain, user_uid)
    await _sql("DELETE FROM Member WHERE domain = :d AND user_uid = :u", d=domain, u=user_uid)
    _forget(user_uid)
    await _load_trusted()


async def _keep_an_owner(domain: str, losing: str) -> None:
    owners = [r["user_uid"] for r in await _sql("SELECT user_uid FROM Member WHERE domain = :d AND `role` = 'owner'",
                                                d=domain)]
    if owners == [losing]:
        raise AccountError("A domain needs an owner — make someone else owner first", 409)


async def forget_domain(domain: str) -> None:
    """A deleted domain's memberships."""
    await _sql("DELETE FROM Member WHERE domain = :d", d=domain)
    _forget()
    await _load_trusted()


# ── Trust ──────────────────────────────────────────────────────────────────

_open_install = True   # no account yet (the first one is always an admin): everything is the operator's


async def _load_trusted() -> None:
    global _trusted, _open_install
    admins = [r["uid"] for r in await _sql("SELECT uid FROM Account WHERE admin = true")]
    rows = await _sql("SELECT domain FROM Member WHERE `role` = 'owner' AND user_uid IN :a", a=admins) if admins else []
    _trusted, _open_install = {r["domain"] for r in rows}, not admins


def trusted(domain: str) -> bool:
    """Owned by an admin: may use the operator's API keys and the OpenHands Coder."""
    return _open_install or domain in _trusted
