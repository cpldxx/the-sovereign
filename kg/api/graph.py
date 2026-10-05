"""Graph API — the whole entity graph of a domain, for visualization."""

from fastapi import APIRouter, HTTPException, Request

from core import database as kgdb
from domains.registry import load_domain

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/{domain_name}")
async def get_graph(domain_name: str, req: Request):
    """
    {
      "entities": [{"uid", "name", "type", "summary", "aliases", "mentions", ...}],
      "facts":    [{"uid", "relation", "fact", "source_uid", "target_uid", "weight", "evidence",
                    "sources", "valid", ...}]       # superseded facts included with valid=false
    }
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"domain": domain_name, **await kgdb.graph(req.app.state.db, domain_name)}
