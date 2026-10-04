"""Ingest pipeline routes."""

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from agents.architect import suggest_all_edges
from agents.gatekeeper import validate_nodes_batch
from agents.ingestor import ingest_raw_data
from core.database import store_edge, store_node
from core.schema import SovereignEdge, SovereignNode
from domains.registry import load_domain
from integrations.openhands import generate_domain_tools

router = APIRouter(prefix="/domains", tags=["pipeline"])

DOMAINS_DIR = Path(__file__).parent.parent / "domains"

# In-memory job store
_jobs: dict[str, dict] = {}


class IngestRequest(BaseModel):
    raw_text: str
    source: str = "user_input"


@router.post("/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest, req: Request):
    """
    Full pipeline: Ingest → Batch Validate → Store → Discover all edges
    3 LLM calls total regardless of node count.
    """
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    config = domain["config"]
    prompts = domain["prompts"]
    ontology = domain.get("ontology")
    db = req.app.state.db

    # Step 1: Ingestor — 1 LLM call → all nodes
    nodes = await ingest_raw_data(request.raw_text, config, prompts, ontology)
    if not nodes:
        return {"domain": domain_name, "total": 0, "stored": 0, "rejected": 0, "edges_created": 0, "details": []}

    # Step 2: Gatekeeper — 1 LLM call → validate all nodes at once
    validations = await validate_nodes_batch(nodes, config, prompts, ontology)

    stored_nodes: list[SovereignNode] = []
    results = []

    for node, validation in zip(nodes, validations):
        if not validation.is_valid:
            results.append({"uid": node.uid, "status": "rejected", "reason": validation.reason})
            continue

        node.reliability = validation.corrected_reliability
        await store_node(db, node)
        stored_nodes.append(node)
        results.append({"uid": node.uid, "status": "stored", "reliability": node.reliability})

    # Step 3: Architect — 1 LLM call → all edges at once
    edges_created = 0
    if len(stored_nodes) > 1:
        suggestions = await suggest_all_edges(stored_nodes, config, prompts, ontology)
        for s in suggestions:
            edge = SovereignEdge(
                from_node=s.from_uid,
                to_node=s.to_uid,
                relation=s.relation,
                weight=s.weight,
            )
            await store_edge(db, edge)
            edges_created += 1

    return {
        "domain": domain_name,
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] == "stored"),
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "edges_created": edges_created,
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
