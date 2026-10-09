"""Playbooks — pre-computed "if this happens, do that" rules, written nightly from the graph."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from core import crew, playbooks
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["playbooks"])


def _require(domain: str) -> None:
    try:
        load_domain(domain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class CycleRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=24 * 31)
    refresh: bool = Field(default=True, description="Rewrite the playbooks after evaluating them")
    wait: bool = Field(default=False, description="Run synchronously and return the outcome")


class StatusChange(BaseModel):
    status: Literal["active", "retired"]


@router.get("/{domain_name}/playbooks")
async def list_playbooks(domain_name: str, req: Request, status: str | None = "active"):
    """Playbooks (active by default; status=retired, or empty for all). `running`: a cycle is in progress."""
    _require(domain_name)
    return {"playbooks": await playbooks.list_playbooks(req.app.state.db, domain_name, status or None),
            "running": playbooks.running(domain_name)}


@router.post("/{domain_name}/playbooks/cycle", status_code=202)
async def run_cycle(domain_name: str, req: Request, background_tasks: BackgroundTasks,
                    request: CycleRequest | None = None):
    """Evaluate the playbooks against the last `hours` of changes (triggered ones become proposals), then
    rewrite them from the graph. Minutes with a local model: background unless `wait: true`."""
    _require(domain_name)
    request = request or CycleRequest()
    if playbooks.running(domain_name):
        raise HTTPException(status_code=409, detail=f"A playbook cycle for {domain_name} is already running")
    if request.wait:
        return await playbooks.cycle(req.app.state.db, domain_name, request.hours, request.refresh)

    async def run() -> None:
        try:
            done = await playbooks.cycle(req.app.state.db, domain_name, request.hours, request.refresh)
            if proposals := (done.get("evaluate") or {}).get("proposals"):
                await crew.notify(req.app.state.db, domain_name, "watcher",
                                  f"{len(proposals)} playbook(s) fired on the last {request.hours} h of knowledge — "
                                  "proposals wait in Actions", {"proposals": proposals[:20]})
        except Exception as e:
            print(f"[KG] playbook cycle for {domain_name} failed: {type(e).__name__}: {e}", flush=True)

    background_tasks.add_task(run)
    return {"domain": domain_name, "status": "running"}


@router.patch("/{domain_name}/playbooks/{uid}")
async def set_status(domain_name: str, uid: str, change: StatusChange, req: Request):
    """Retire a playbook you don't want (or reactivate it)."""
    _require(domain_name)
    if not await playbooks.set_status(req.app.state.db, domain_name, uid, change.status):
        raise HTTPException(status_code=404, detail=f"Playbook {uid} not found")
    return {"uid": uid, "status": change.status}
