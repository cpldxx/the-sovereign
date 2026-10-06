"""Daily reports — what changed in a domain's graph over a period, as a briefing to read and to speak."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import database as kgdb
from core import digest
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["reports"])


class ReportRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=24 * 31)
    refresh: bool = Field(default=True, description="Rewrite stale entity summaries first")
    wait: bool = Field(default=False, description="Generate synchronously and return the report")


def _require(domain: str) -> None:
    try:
        load_domain(domain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{domain_name}/reports")
async def list_reports(domain_name: str, req: Request, limit: int = Query(30, ge=1, le=200)):
    """Reports, newest first (without their item lists). `generating`: a report is being written now."""
    _require(domain_name)
    return {"reports": await kgdb.list_reports(req.app.state.db, domain_name, limit),
            "generating": digest.running(domain_name)}


@router.get("/{domain_name}/reports/{uid}")
async def get_report(domain_name: str, uid: str, req: Request):
    """One report with its digest: the sources, entities and facts behind it."""
    _require(domain_name)
    if not (report := await kgdb.get_report(req.app.state.db, domain_name, uid)):
        raise HTTPException(status_code=404, detail=f"Report {uid} not found")
    return report


@router.post("/{domain_name}/reports", status_code=202)
async def create_report(domain_name: str, req: Request, background_tasks: BackgroundTasks,
                        request: ReportRequest | None = None):
    """Refresh stale entity summaries, then write the report for the last `hours`. Runs in the background
    (minutes with a local model) unless `wait: true`, which returns the report."""
    _require(domain_name)
    request = request or ReportRequest()
    if digest.running(domain_name):
        raise HTTPException(status_code=409, detail=f"A report for {domain_name} is already being generated")
    if request.wait:
        return await digest.build_report(req.app.state.db, domain_name, request.hours, request.refresh)

    async def run() -> None:
        try:
            await digest.build_report(req.app.state.db, domain_name, request.hours, request.refresh)
        except Exception as e:
            print(f"[KG] report for {domain_name} failed: {type(e).__name__}: {e}", flush=True)

    background_tasks.add_task(run)
    return {"domain": domain_name, "status": "generating"}
