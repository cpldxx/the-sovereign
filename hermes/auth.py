"""Shared-token auth between the Sovereign services and the UI (SOVEREIGN_TOKEN; off when unset).

Every request needs "Authorization: Bearer <token>" — except /health and CORS preflights. Calls to the other
Sovereign services carry it via headers(). Phase G replaces this single local token with real users.
"""

import hmac
import os

from dotenv import load_dotenv
from starlette.responses import JSONResponse

load_dotenv()
TOKEN = os.getenv("SOVEREIGN_TOKEN", "").strip()
PUBLIC = ("/health",)


def headers() -> dict:
    """Headers for calls to the other Sovereign services."""
    return {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


class TokenAuth:
    """Pure ASGI middleware (safe for streaming responses: SSE, MCP)."""

    def __init__(self, app):
        self.app = app
        self.expected = f"Bearer {TOKEN}".encode()

    async def __call__(self, scope, receive, send):
        if (TOKEN and scope["type"] == "http" and scope["method"] != "OPTIONS"
                and scope["path"] not in PUBLIC):
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await JSONResponse({"detail": "Missing or wrong SOVEREIGN_TOKEN"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)
