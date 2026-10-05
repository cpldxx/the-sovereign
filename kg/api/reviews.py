"""Head review queue — ambiguous writes wait here: weak facts, unsure entity merges, unsure fact links."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import database as kgdb
from core import kg
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["reviews"])


class Decision(BaseModel):
    approve: bool
    note: str = ""


@router.get("/{domain_name}/reviews")
async def list_reviews(domain_name: str, req: Request, status: Literal["pending", "approved", "rejected"] = "pending"):
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"reviews": await kgdb.list_reviews(req.app.state.db, domain_name, status)}


@router.post("/{domain_name}/reviews/{uid}")
async def decide(domain_name: str, uid: str, decision: Decision, req: Request):
    """approve → apply it (add the fact / merge the entities / link the facts); reject → dismiss."""
    try:
        load_domain(domain_name)
        return await kg.resolve_review(req.app.state.db, domain_name, uid, decision.approve, decision.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
