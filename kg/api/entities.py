"""Entity and episode routes — what the graph knows about one thing, and where it came from."""

from fastapi import APIRouter, HTTPException, Query, Request

from core import database as kgdb
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["entities"])


def _require(domain: str) -> None:
    try:
        load_domain(domain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{domain_name}/entities/{uid}")
async def get_entity(domain_name: str, uid: str, req: Request):
    """The entity, every fact touching it (strongest first, superseded included), and the episodes mentioning it."""
    _require(domain_name)
    detail = await kgdb.entity_detail(req.app.state.db, domain_name, uid)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Entity {uid} not found")
    return detail


@router.get("/{domain_name}/episodes")
async def list_episodes(domain_name: str, req: Request, limit: int = Query(50, ge=1, le=500)):
    """Ingested sources, newest first."""
    _require(domain_name)
    return {"episodes": await kgdb.list_episodes(req.app.state.db, domain_name, limit)}
