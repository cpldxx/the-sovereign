"""The Sovereign - Main entry point.

Phase 1: FastAPI server + basic pipeline (Ingest -> Validate -> Store)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from agents.gatekeeper import validate_node
from agents.ingestor import ingest_raw_data
from core.database import get_db, query_nodes, store_node


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage DB connection on server start/stop"""
    app.state.db = await get_db()
    yield
    await app.state.db.close()


app = FastAPI(
    title="The Sovereign",
    description="Autonomous intelligence system - 100B node knowledge graph",
    version="0.1.0",
    lifespan=lifespan,
)


class IngestRequest(BaseModel):
    raw_text: str
    domain: str
    source: str


@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


@app.post("/ingest")
async def ingest(request: IngestRequest):
    """Phase 1 pipeline: Ingest -> Validate -> Store"""

    # Step 1: Ingestor - convert raw data to nodes
    nodes = await ingest_raw_data(request.raw_text, request.domain, request.source)

    results = []
    for node in nodes:
        # Step 2: Gatekeeper - validate each node
        validation = await validate_node(node)

        if not validation.is_valid:
            results.append({
                "uid": node.uid,
                "status": "rejected",
                "reason": validation.reason,
            })
            continue

        # Apply corrected reliability
        node.reliability = validation.corrected_reliability

        # Step 3: Store in DB
        stored = await store_node(app.state.db, node)
        results.append({
            "uid": node.uid,
            "status": "stored",
            "reliability": node.reliability,
        })

    return {
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] == "stored"),
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "details": results,
    }


@app.get("/nodes")
async def list_nodes(domain: str | None = None):
    """Query stored nodes"""
    nodes = await query_nodes(app.state.db, domain)
    return {"nodes": nodes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
