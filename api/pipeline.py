"""Ingest pipeline routes."""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from agents.gatekeeper import validate_node
from agents.ingestor import ingest_raw_data
from core.database import store_node
from domains.registry import load_domain
from tools.openhands import generate_domain_tools

router = APIRouter(prefix="/domains", tags=["pipeline"])

DOMAINS_DIR = Path(__file__).parent.parent / "domains"

# In-memory job store: job_id -> {"status": ..., "domain": ..., "result": ..., "error": ...}
_jobs: dict[str, dict] = {}


class IngestRequest(BaseModel):
    raw_text: str
    source: str = "user_input"


@router.post("/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest, req: Request):
    """Run the full pipeline: Ingest -> Validate -> Store"""
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    config = domain["config"]
    prompts = domain["prompts"]
    db = req.app.state.db

    # Step 1: Ingestor
    nodes = await ingest_raw_data(request.raw_text, config, prompts)

    results = []
    for node in nodes:
        # Step 2: Gatekeeper
        validation = await validate_node(node, config, prompts)

        if not validation.is_valid:
            results.append({
                "uid": node.uid,
                "status": "rejected",
                "reason": validation.reason,
            })
            continue

        node.reliability = validation.corrected_reliability

        # Step 3: Store
        await store_node(db, node)
        results.append({
            "uid": node.uid,
            "status": "stored",
            "reliability": node.reliability,
        })

    return {
        "domain": domain_name,
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] == "stored"),
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "details": results,
    }


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
    """
    Kick off OpenHands code generation in the background.
    Returns immediately with a job_id. Poll /generate-tools/status/{job_id} for result.
    """
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
