"""Domain management routes."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from agents.ontologist import bootstrap_domain_ontology
from core.database import domain_stats, drop_domain_db, ensure_domain_db
from domains.registry import create_domain, delete_domain, list_domains, load_domain, ontology_path

router = APIRouter(prefix="/domains", tags=["domains"])


class CreateDomainRequest(BaseModel):
    name: str = Field(..., description="Display name; the domain id is derived from it ('Quant Trading' -> 'quant_trading')")
    description: str
    data_sources: list[str] = []
    keywords: list[str] = []


@router.post("")
async def api_create_domain(request: CreateDomainRequest, background_tasks: BackgroundTasks, req: Request):
    """Create a new domain workspace + its ArcadeDB database, then generate the ontology in the background."""
    try:
        domain = create_domain(request.name, request.description, request.data_sources, request.keywords)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await ensure_domain_db(req.app.state.db, domain)
    except Exception as e:
        delete_domain(domain)
        raise HTTPException(status_code=500, detail=f"Domain database creation failed: {e}")

    background_tasks.add_task(bootstrap_domain_ontology, domain, request.description)
    return {"status": "created", "domain": domain}


def _summary(domain: str) -> dict:
    return {
        "id": domain,
        "description": load_domain(domain)["config"].get("description", ""),
        "ontology_generated": ontology_path(domain).exists(),
    }


@router.get("")
async def api_list_domains():
    """List all domains: id, description, and whether the ontology has been generated yet."""
    return {"domains": [_summary(d) for d in list_domains()]}


@router.get("/{domain_name}")
async def api_get_domain(domain_name: str, req: Request):
    """Domain config, ontology status, and KG stats (node/edge counts, nodes per category)."""
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "domain": domain_name,
        "config": domain["config"],
        "ontology_generated": ontology_path(domain_name).exists(),
        "stats": await domain_stats(req.app.state.db, domain_name),
    }


@router.delete("/{domain_name}")
async def api_delete_domain(domain_name: str, req: Request):
    """Delete a domain workspace and drop its ArcadeDB database."""
    try:
        delete_domain(domain_name)
        await drop_domain_db(req.app.state.db, domain_name)
        return {"status": "deleted", "domain": domain_name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
