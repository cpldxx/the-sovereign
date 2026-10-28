"""The research charter (core/charter.py): what the user wants from a domain — drafted from the Head's interview,
confirmed by the user, then steering every research run."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import auth, charter

router = APIRouter(tags=["charter"])


class Draft(BaseModel):
    notes: str = Field("", max_length=8000, description="What the user said they want (none: from the description)")


class Decision(BaseModel):
    accept: bool


def _fail(e: Exception):
    raise HTTPException(status_code=422, detail=str(e))


@router.get("/domains/{domain_name}/charter")
async def api_charter(domain_name: str, req: Request):
    """The charter, its status (none, draft, active), the Head's suggestions waiting for the user, and `brief`: the
    domain as research prompts see it."""
    auth.require(domain_name)
    db = req.app.state.db
    return {**await charter.get(db, domain_name), "brief": await charter.brief(db, domain_name)}


@router.put("/domains/{domain_name}/charter")
async def api_save(domain_name: str, changes: dict, req: Request):
    """Edit it: goal, questions, scope, exclude, depth (overview | working | expert)."""
    auth.require(domain_name, "editor")
    try:
        return await charter.save(req.app.state.db, domain_name, changes)
    except (charter.CharterError, TypeError) as e:
        _fail(e)


@router.post("/domains/{domain_name}/charter/draft")
async def api_draft(domain_name: str, body: Draft, req: Request):
    """Write it from interview notes (a minute with a local model). An active charter is revised in place."""
    auth.require(domain_name, "editor")
    try:
        return await charter.draft(req.app.state.db, domain_name, body.notes)
    except charter.CharterError as e:
        _fail(e)


@router.post("/domains/{domain_name}/charter/confirm")
async def api_confirm(domain_name: str, req: Request):
    """The user agrees: it becomes active, and the first time the domain's first deep research starts (`kickoff`)."""
    auth.require(domain_name, "editor")
    try:
        return await charter.start(req.app.state.db, domain_name)
    except charter.CharterError as e:
        _fail(e)


@router.post("/domains/{domain_name}/charter/suggestions/{uid}")
async def api_decide(domain_name: str, uid: str, body: Decision, req: Request):
    """Take or leave one of the Head's suggestions."""
    auth.require(domain_name, "editor")
    try:
        return await charter.decide(req.app.state.db, domain_name, uid, body.accept)
    except charter.CharterError as e:
        _fail(e)


@router.delete("/domains/{domain_name}/charter")
async def api_reset(domain_name: str, req: Request):
    """Start over: the Head interviews again. What the graph learned stays."""
    auth.require(domain_name, "editor")
    return await charter.reset(req.app.state.db, domain_name)
