"""The Sovereign - Main entry point.

Multi-tenant SaaS with domain-specific AI workspaces.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agents.gatekeeper import validate_node
from agents.ingestor import ingest_raw_data
from core.database import get_db, query_nodes, store_node
from domains.registry import create_domain, delete_domain, list_domains, load_domain


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage DB connection on server start/stop"""
    app.state.db = await get_db()
    yield
    await app.state.db.close()


app = FastAPI(
    title="The Sovereign",
    description="Multi-tenant AI workspace with domain-specific agents",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Request Models ---

class CreateDomainRequest(BaseModel):
    name: str
    description: str
    data_sources: list[str] = []
    keywords: list[str] = []


class IngestRequest(BaseModel):
    raw_text: str
    source: str = "user_input"


# --- Domain Management ---

@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


@app.post("/domains")
async def api_create_domain(request: CreateDomainRequest):
    """Create a new domain workspace"""
    try:
        create_domain(request.name, request.description, request.data_sources, request.keywords)
        return {"status": "created", "domain": request.name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/domains")
async def api_list_domains():
    """List all domains"""
    return {"domains": list_domains()}


@app.delete("/domains/{domain_name}")
async def api_delete_domain(domain_name: str):
    """Delete a domain workspace"""
    try:
        delete_domain(domain_name)
        return {"status": "deleted", "domain": domain_name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Ingestion Pipeline ---

@app.post("/domains/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest):
    """Ingest data into a specific domain: Ingest -> Validate -> Store"""
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    config = domain["config"]
    prompts = domain["prompts"]

    # Step 1: Ingestor - convert raw data to nodes
    nodes = await ingest_raw_data(request.raw_text, config, prompts)

    results = []
    for node in nodes:
        # Step 2: Gatekeeper - validate each node
        validation = await validate_node(node, config, prompts)

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
        "domain": domain_name,
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] == "stored"),
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "details": results,
    }


@app.get("/domains/{domain_name}/nodes")
async def get_domain_nodes(domain_name: str):
    """Query nodes in a specific domain"""
    nodes = await query_nodes(app.state.db, domain_name)
    return {"domain": domain_name, "nodes": nodes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
