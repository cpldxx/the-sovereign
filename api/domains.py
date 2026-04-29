"""Domain management routes."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from domains.registry import create_domain, delete_domain, list_domains, load_domain

router = APIRouter(prefix="/domains", tags=["domains"])


class CreateDomainRequest(BaseModel):
    name: str
    description: str
    data_sources: list[str] = []
    keywords: list[str] = []


@router.post("")
async def api_create_domain(request: CreateDomainRequest):
    """Create a new domain workspace"""
    try:
        create_domain(request.name, request.description, request.data_sources, request.keywords)
        return {"status": "created", "domain": request.name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
async def api_delete_domain(domain_name: str):
    """Delete a domain workspace"""
    try:
        delete_domain(domain_name)
        return {"status": "deleted", "domain": domain_name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
