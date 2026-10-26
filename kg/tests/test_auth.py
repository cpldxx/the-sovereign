"""Who may do what: the role a path needs, and the middleware that enforces it (no services needed)."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core import auth
from core.auth import Principal

ORIGIN = auth.ORIGINS[0]


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/domains/a", ("a", "viewer")),
    ("GET", "/graph/a", ("a", "viewer")),
    ("POST", "/domains/a/query", ("a", "viewer")),
    ("POST", "/domains/a/ask/stream", ("a", "viewer")),
    ("POST", "/domains/a/sensors/x/read", ("a", "viewer")),
    ("POST", "/domains/a/sources", ("a", "viewer")),          # which URLs are already read: a lookup
    ("POST", "/domains/a/ingest", ("a", "editor")),
    ("POST", "/domains/a/research", ("a", "editor")),
    ("POST", "/domains/a/proposals/p1", ("a", "editor")),
    ("POST", "/domains/a/sensors/check", ("a", "editor")),
    ("DELETE", "/domains/a", ("a", "owner")),
    ("PUT", "/domains/a/members", ("a", "owner")),
    ("POST", "/domains/a/actions", ("a", "owner")),
    ("DELETE", "/domains/a/actions/hook", ("a", "owner")),
    ("DELETE", "/domains/a/members/u1", ("a", "viewer")),   # leaving; the handler checks whose
    ("GET", "/domains", None),
    ("GET", "/auth/me", None),
])
def test_needed(method, path, expected):
    assert auth.needed(method, path) == expected


def test_roles_and_denial():
    viewer = Principal("user", {"uid": "u"}, {"a": "viewer"})
    assert viewer.can("a") and not viewer.can("a", "editor")
    assert auth.denial(viewer, "b") == (404, "Domain 'b' does not exist")       # not a member: as if missing
    assert auth.denial(viewer, "a", "editor")[0] == 403
    scoped = Principal("service", scope="a")
    assert scoped.can("a", "owner") and auth.denial(scoped, "b")[0] == 404     # a Head can't leave its domain
    assert Principal("service").can("anything", "owner")
    assert viewer.visible(["a", "b"]) == ["a"] and scoped.visible(["a", "b"]) == ["a"]


def _app(principals: dict[str, Principal]):
    async def fake(token: str, fresh: bool = False):
        return principals.get(token)

    auth.use(fake)

    async def ok(request):
        return JSONResponse({"ok": True})

    async def ws(websocket):
        await websocket.accept()
        await websocket.send_json({"principal": websocket.scope["state"]["principal"].kind})
        await websocket.close()

    async def logout(request):
        principal = auth.current.get()
        return JSONResponse({"principal": principal.kind if principal else None})

    app = Starlette(routes=[Route("/auth/logout", logout, methods=["POST"]), WebSocketRoute("/ws/{path:path}", ws),
                            Route("/{path:path}", ok, methods=["GET", "POST", "PUT", "DELETE"])])
    app.add_middleware(auth.Auth)
    return TestClient(app)


@pytest.fixture
def client():
    before = auth._authenticator
    yield _app({"viewer": Principal("user", {"uid": "v"}, {"a": "viewer"}),
                "editor": Principal("user", {"uid": "e"}, {"a": "editor"})})
    auth.use(before)


def test_anonymous_and_public(client):
    assert client.get("/domains").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.post("/auth/login").status_code == 200
    assert client.get("/domains", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_roles_enforced(client):
    bearer = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    assert client.get("/domains/a", headers=bearer("viewer")).status_code == 200
    assert client.post("/domains/a/ingest", headers=bearer("viewer")).status_code == 403
    assert client.post("/domains/a/ingest", headers=bearer("editor")).status_code == 200
    assert client.delete("/domains/a", headers=bearer("editor")).status_code == 403
    assert client.get("/domains/b", headers=bearer("editor")).status_code == 404


def test_service_token_and_scope(client):
    service = {"Authorization": f"Bearer {auth.TOKEN}"}
    assert client.delete("/domains/a", headers=service).status_code == 200
    scoped = {**service, "X-Sovereign-Domain": "a"}
    assert client.get("/domains/a", headers=scoped).status_code == 200
    assert client.get("/domains/b", headers=scoped).status_code == 404


def test_service_token_never_as_cookie(client):
    client.cookies.set(auth.COOKIE, auth.TOKEN)
    assert client.get("/domains/a").status_code == 401


def test_cookie_writes_need_our_origin(client):
    client.cookies.set(auth.COOKIE, "editor")
    assert client.get("/domains/a", headers={"Origin": "https://evil.example"}).status_code == 200   # reads are fine
    assert client.post("/domains/a/ingest", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/domains/a/ingest", headers={"Origin": ORIGIN}).status_code == 200


def test_websockets_need_credentials(client):
    """2026-10-14: websocket scopes passed the middleware unchecked."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/domains/a"):
            pass
    with pytest.raises(WebSocketDisconnect):                                   # not a member: refused too
        with client.websocket_connect("/ws/x", headers={"Authorization": "Bearer nope"}):
            pass
    with client.websocket_connect("/ws/x", headers={"Authorization": "Bearer viewer"}) as ws:
        assert ws.receive_json() == {"principal": "user"}
    client.cookies.set(auth.COOKIE, "viewer")
    with pytest.raises(WebSocketDisconnect):                                   # a cross-site page with our cookie
        with client.websocket_connect("/ws/x", headers={"Origin": "https://evil.example"}):
            pass


def test_logout_works_signed_out(client):
    """An expired cookie must still be clearable: logout passes without valid credentials, as nobody."""
    assert client.post("/auth/logout").json() == {"principal": None}
    client.cookies.set(auth.COOKIE, "expired")
    assert client.post("/auth/logout").json() == {"principal": None}
    assert client.post("/auth/logout", headers={"Authorization": "Bearer editor"}).json() == {"principal": "user"}
