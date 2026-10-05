"""Ingest pipeline routes."""

import uuid
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from core import kg
from domains.registry import DOMAINS_DIR, load_domain
from integrations.openhands import generate_domain_tools

router = APIRouter(prefix="/domains", tags=["pipeline"])

# In-memory job store
_jobs: dict[str, dict] = {}


class IngestRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)
    source: str = "user_input"
    title: str | None = None
    content_status: Literal["full", "partial"] = "full"


@router.post("/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest, req: Request):
    """
    Store the text as an episode and grow the neuron graph from it:
    extract → ontology/text gates → validate → resolve entities → link facts → commit / Head review.
    Usually 2 LLM calls (+1 per ambiguity kind) and 1 embedding call per ~8k-char chunk.
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await kg.ingest(
        req.app.state.db, domain_name, request.raw_text, request.source, request.title, request.content_status
    )


async def _run_generate_job(job_id: str, domain_name: str, config: dict, tools_path: Path):
    """Background task: run OpenHands and update job state when done."""
    try:
        generated_code = await generate_domain_tools(domain_name, config)
        tools_path.write_text(generated_code)
        _jobs[job_id] = {
            "status": "done",
            "domain": domain_name,
            "lines": generated_code.count("\n") + 1,
        }
    except Exception as e:
        _jobs[job_id] = {
            "status": "failed",
            "domain": domain_name,
            "error": str(e)[:500],
        }


@router.post("/{domain_name}/generate-tools")
async def generate_tools(domain_name: str, background_tasks: BackgroundTasks):
    """Kick off OpenHands code generation in the background."""
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    tools_path = DOMAINS_DIR / domain_name / "tools.py"
    if not tools_path.parent.exists():
        raise HTTPException(status_code=404, detail=f"Domain directory not found: {domain_name}")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "generating", "domain": domain_name}

    background_tasks.add_task(
        _run_generate_job, job_id, domain_name, domain["config"], tools_path
    )

    return {"status": "generating", "job_id": job_id, "domain": domain_name}


@router.get("/generate-tools/status/{job_id}")
async def generate_tools_status(job_id: str):
    """Check the status of a generate-tools job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return job
