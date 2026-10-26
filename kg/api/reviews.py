"""Head review queue — ambiguous writes wait here: weak facts, unsure entity merges, unsure fact links."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core import database as kgdb
from core import kg
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["reviews"])


class Decision(BaseModel):
    approve: bool
    note: str = ""


@router.get("/{domain_name}/reviews")
async def list_reviews(domain_name: str, req: Request, status: Literal["pending", "approved", "rejected"] = "pending",
                       limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                       order: Literal["oldest", "newest"] = "newest"):
    """A page of the queue and how many items it holds in all (`total`) — a long queue is more than one page."""
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db = req.app.state.db
    return {"reviews": await kgdb.list_reviews(db, domain_name, status, limit, offset, oldest_first=order == "oldest"),
            "total": await kgdb.count_reviews(db, domain_name, status)}


@router.post("/{domain_name}/reviews/{uid}")
async def decide(domain_name: str, uid: str, decision: Decision, req: Request):
    """approve → apply it (add the fact / merge the entities / link the facts); reject → dismiss."""
    try:
        load_domain(domain_name)
        return await kg.resolve_review(req.app.state.db, domain_name, uid, decision.approve, decision.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
