"""The Sovereign - Main entry point."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.actions import router as actions_router
from api.domains import router as domains_router
from api.entities import router as entities_router
from api import mcp as kg_mcp
from api.graph import router as graph_router
from api.ontology import router as ontology_router
from api.playbooks import router as playbooks_router
from api.pipeline import router as pipeline_router
from api.query import router as query_router
from api.reports import router as reports_router
from api.reviews import router as reviews_router
from api.sensors import router as sensors_router
from core import playbooks, tracing
from core.database import get_db


# Built at import time: a server's MCP session manager only exists after this call.
mcp_routes = [route for role, server in kg_mcp.servers.items()
              for route in server.streamable_http_app(streamable_http_path=kg_mcp.PATHS[role]).routes]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if tracing.setup():
        print("[KG] tracing to LangFuse", flush=True)
    app.state.db = kg_mcp.db = await get_db()
    # Live playbook triggers: sensors checked around the clock (SENSOR_WATCH=off disables it).
    watch = asyncio.create_task(playbooks.watch_loop(app.state.db)) if playbooks.WATCH else None
    # The MCP apps' own lifespans never run (only their routes are served), so the host enters their
    # session managers.
    async with AsyncExitStack() as stack:
        for server in kg_mcp.servers.values():
            await stack.enter_async_context(server.session_manager.run())
        yield
    if watch:
        watch.cancel()
    await app.state.db.close()
    tracing.shutdown()


app = FastAPI(
    title="The Sovereign",
    description="Multi-tenant AI workspace with domain-specific agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(domains_router)
app.include_router(pipeline_router)
app.include_router(graph_router)
app.include_router(query_router)
app.include_router(ontology_router)
app.include_router(entities_router)
app.include_router(reviews_router)
app.include_router(reports_router)
app.include_router(actions_router)
app.include_router(playbooks_router)
app.include_router(sensors_router)


@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


@app.get("/health")
async def health():
    """KG API liveness plus ArcadeDB readiness."""
    return {"kg": True, "arcadedb": await app.state.db.ready(), "tracing": tracing.ENABLED}


# MCP endpoints for agents, one per role: /mcp (Head), /mcp/readonly.
app.router.routes.extend(mcp_routes)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
