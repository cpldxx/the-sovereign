"""Actions — the catalog, proposals, and the user's decisions on them.

Deciding a proposal exists only here, in the user's API (the UI's Actions tab): no agent tool can
confirm or execute an action.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import actions, auth
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["actions"])


def _require(domain: str) -> None:
    try:
        load_domain(domain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class ParamSpec(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: str = ""
    required: bool = True


class WebhookAction(BaseModel):
    name: str = Field(description="Identifier agents use, e.g. notify_slack")
    description: str = Field(description="What it does — agents read this to decide when to propose it")
    url: str = Field(description="Receives a JSON POST: {domain, action, params, dry_run, proposal, rationale}")
    params: list[ParamSpec] = Field(default_factory=list)
    dry_run: bool = Field(default=False, description="The endpoint supports dry_run: true (used for previews)")


class Decision(BaseModel):
    approve: bool
    note: str = ""


class ProposalRequest(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)
    rationale: str = ""


@router.get("/{domain_name}/actions")
async def list_actions(domain_name: str, req: Request):
    """Everything the domain can do: built-in actions and the webhooks (their URLs only for owners — a URL is
    often a secret, like a Slack webhook)."""
    _require(domain_name)
    owner = auth.current.get().can(domain_name, "owner")
    return {"actions": await actions.catalog(req.app.state.db, domain_name, with_urls=owner)}


@router.post("/{domain_name}/actions", status_code=201)
async def add_action(domain_name: str, action: WebhookAction, req: Request):
    _require(domain_name)
    try:
        return await actions.add_webhook(req.app.state.db, domain_name, action.name, action.description, action.url,
                                         [p.model_dump() for p in action.params], action.dry_run)
    except actions.ActionError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/{domain_name}/actions/{name}")
async def remove_action(domain_name: str, name: str, req: Request):
    _require(domain_name)
    try:
        await actions.remove_webhook(req.app.state.db, domain_name, name)
    except actions.ActionError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "deleted", "name": name}


@router.get("/{domain_name}/proposals")
async def list_proposals(domain_name: str, req: Request, status: str | None = None,
                         limit: int = Query(50, ge=1, le=500)):
    """Proposals, newest first. status: proposed (waiting for you) | executed | rejected | failed | expired."""
    _require(domain_name)
    return {"proposals": await actions.list_proposals(req.app.state.db, domain_name, status, limit)}


@router.post("/{domain_name}/proposals", status_code=201)
async def create_proposal(domain_name: str, request: ProposalRequest, req: Request):
    """Propose an action yourself (it still shows its preview before you confirm it)."""
    _require(domain_name)
    try:
        return await actions.propose(req.app.state.db, domain_name, request.action, request.params,
                                     request.rationale, source="user")
    except actions.ActionError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/{domain_name}/proposals/{uid}")
async def decide(domain_name: str, uid: str, decision: Decision, req: Request):
    """approve → execute exactly as previewed (once); reject → close it."""
    _require(domain_name)
    try:
        return await actions.decide(req.app.state.db, domain_name, uid, decision.approve, decision.note)
    except actions.ActionError as e:
        raise HTTPException(status_code=409, detail=str(e))
