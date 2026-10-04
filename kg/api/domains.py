"""Domain management routes."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from agents.ontologist import bootstrap_domain_ontology
from core.database import drop_domain_db, ensure_domain_db
from domains.registry import DOMAINS_DIR, create_domain, delete_domain, list_domains, load_domain

router = APIRouter(prefix="/domains", tags=["domains"])


class CreateDomainRequest(BaseModel):
    name: str
    description: str
    data_sources: list[str] = []
    keywords: list[str] = []


@router.post("")
async def api_create_domain(request: CreateDomainRequest, background_tasks: BackgroundTasks, req: Request):
    """Create a new domain workspace + its ArcadeDB database, then generate the ontology in the background."""
    try:
        create_domain(request.name, request.description, request.data_sources, request.keywords)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await ensure_domain_db(req.app.state.db, request.name)
    except Exception as e:
        delete_domain(request.name)
        raise HTTPException(status_code=500, detail=f"Domain database creation failed: {e}")

    background_tasks.add_task(
        bootstrap_domain_ontology,
        DOMAINS_DIR / request.name,
        request.name,
        request.description,
    )
    return {"status": "created", "domain": request.name}


@router.get("")
async def api_list_domains():
    """List all domains"""
    return {"domains": list_domains()}


@router.get("/{domain_name}")
async def api_get_domain(domain_name: str):
    """Get domain config"""
    try:
        domain = load_domain(domain_name)
        return {"domain": domain_name, "config": domain["config"]}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{domain_name}")
async def api_delete_domain(domain_name: str, req: Request):
    """Delete a domain workspace and drop its ArcadeDB database."""
    try:
        delete_domain(domain_name)
        await drop_domain_db(req.app.state.db, domain_name)
        return {"status": "deleted", "domain": domain_name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
