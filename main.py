"""The Sovereign - Main entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.domains import router as domains_router
from api.graph import router as graph_router
from api.pipeline import router as pipeline_router
from core.database import get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await get_db()
    yield
    await app.state.db.close()


app = FastAPI(
    title="The Sovereign",
    description="Multi-tenant AI workspace with domain-specific agents",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(domains_router)
app.include_router(pipeline_router)
app.include_router(graph_router)


@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
