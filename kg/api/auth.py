"""Accounts: sign-up, sign-in, sessions, API tokens and invites.

The browser session is an httpOnly cookie (COOKIE_SECURE=true behind HTTPS); scripts and MCP clients use API
tokens as "Authorization: Bearer svk_…". Both reach every service — Hermes and Research ask /auth/me.
"""

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core import accounts, auth
from core.accounts import AccountError

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes", "on")


class SignUp(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    name: str = Field(default="", max_length=80)
    invite: str = Field(default="", max_length=100)


class SignIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordChange(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


class TokenRequest(BaseModel):
    label: str = Field(max_length=80)


def _fail(e: AccountError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


def _user() -> auth.Principal:
    principal = auth.current.get()
    if not principal or principal.kind != "user":
        raise HTTPException(status_code=403, detail="Only for a signed-in person")
    return principal


def _admin() -> auth.Principal:
    principal = _user()
    if not principal.user["admin"]:
        raise HTTPException(status_code=403, detail="Only for an admin")
    return principal


def _start_session(response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=int(accounts.SESSION_DAYS * 86400), httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")


@router.get("/config")
async def config():
    """What the sign-in page offers: whether sign-up needs an invite, and whether no account exists yet (then the
    first sign-up becomes the admin and owns the existing domains)."""
    return {"signup": accounts.SIGNUP, "first_account": not await accounts.has_accounts()}


@router.post("/signup", status_code=201)
async def signup(body: SignUp, response: Response):
    try:
        user, token = await accounts.signup(body.email, body.password, body.name, body.invite)
    except AccountError as e:
        raise _fail(e)
    _start_session(response, token)
    return {"user": user}


@router.post("/login")
async def login(body: SignIn, request: Request, response: Response):
    try:
        user, token = await accounts.login(body.email, body.password, request.client.host if request.client else "")
    except AccountError as e:
        raise _fail(e)
    _start_session(response, token)
    return {"user": user}


@router.post("/logout")
async def logout(request: Request, response: Response):
    if found := auth.credentials(request.headers):
        await accounts.revoke(found[0])
    response.delete_cookie(auth.COOKIE, path="/", samesite="lax", secure=COOKIE_SECURE, httponly=True)
    return {"status": "signed out"}


@router.get("/me")
async def me():
    """Who is calling: the account and its role in each domain (service callers: their scope)."""
    principal = auth.current.get()
    if principal.kind == "service":
        return {"kind": "service", "scope": principal.scope}
    return {"kind": "user", "user": principal.user, "roles": principal.roles}


@router.post("/password")
async def change_password(body: PasswordChange, request: Request):
    """Change the password; other browser sessions are signed out."""
    principal = _user()
    try:
        await accounts.change_password(principal.user["uid"], body.current, body.new,
                                       keep_token=auth.credentials(request.headers)[0])
    except AccountError as e:
        raise _fail(e)
    return {"status": "changed"}


@router.get("/tokens")
async def list_tokens():
    return {"tokens": await accounts.api_tokens(_user().user["uid"])}


@router.post("/tokens", status_code=201)
async def create_token(body: TokenRequest):
    """A personal API token (shown once) for MCP clients and scripts: Authorization: Bearer <token>."""
    try:
        return await accounts.create_api_token(_user().user["uid"], body.label)
    except AccountError as e:
        raise _fail(e)


@router.delete("/tokens/{uid}")
async def revoke_token(uid: str):
    try:
        await accounts.revoke_api_token(_user().user["uid"], uid)
    except AccountError as e:
        raise _fail(e)
    return {"status": "revoked"}


@router.get("/invites")
async def list_invites():
    _admin()
    return {"invites": await accounts.list_invites(), "signup": accounts.SIGNUP}


@router.post("/invites", status_code=201)
async def create_invite():
    """A single-use sign-up code (valid 7 days) for someone you invite — pass the link on yourself."""
    return await accounts.create_invite(_admin().user["uid"])


@router.delete("/invites/{uid}")
async def revoke_invite(uid: str):
    _admin()
    await accounts.revoke_invite(uid)
    return {"status": "revoked"}
