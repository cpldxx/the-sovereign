"""The Sovereign - Main entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.domains import router as domains_router
from api.entities import router as entities_router
from api import mcp as kg_mcp
from api.graph import router as graph_router
from api.ontology import router as ontology_router
from api.pipeline import router as pipeline_router
from api.query import router as query_router
from api.reports import router as reports_router
from api.reviews import router as reviews_router
from core import tracing
from core.database import get_db


# Built at import time: the MCP session manager only exists after this call.
mcp_app = kg_mcp.mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if tracing.setup():
        print("[KG] tracing to LangFuse", flush=True)
    app.state.db = kg_mcp.db = await get_db()
    # A mounted app's own lifespan never runs, so the host enters the MCP session manager.
    async with kg_mcp.mcp.session_manager.run():
        yield
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


@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


@app.get("/health")
async def health():
    """KG API liveness plus ArcadeDB readiness."""
    return {"kg": True, "arcadedb": await app.state.db.ready(), "tracing": tracing.ENABLED}


# MCP endpoint for agents at /mcp. Mounted last: Mount("/") matches every path,
# so it only receives requests no route above claimed.
app.mount("/", mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
