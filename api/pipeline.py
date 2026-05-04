"""Ingest pipeline routes."""

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from agents.architect import EdgeSuggestion, suggest_edges
from agents.gatekeeper import ValidationResult, validate_node
from agents.ingestor import ingest_raw_data
from core.database import store_edge, store_node
from core.schema import SovereignEdge, SovereignNode
from domains.registry import load_domain
from tools.openhands import generate_domain_tools

router = APIRouter(prefix="/domains", tags=["pipeline"])

DOMAINS_DIR = Path(__file__).parent.parent / "domains"

# In-memory job store: job_id -> {"status": ..., "domain": ..., "result": ..., "error": ...}
_jobs: dict[str, dict] = {}


class IngestRequest(BaseModel):
    raw_text: str
    source: str = "user_input"


async def _revalidate_failed(
    failed_nodes: list[SovereignNode],
    domain_config: dict,
    prompts_module,
    db,
) -> list[dict]:
    """
    Batch retry for nodes that failed Gatekeeper validation.
    Uses a simplified prompt with full batch context — more info = better chance of passing.
    Returns results for each retried node.
    """
    if not failed_nodes:
        return []

    from pydantic_ai import Agent
    from agents.gatekeeper import MODEL

    # Build a compact summary of all failed nodes as shared context
    batch_context = "\n".join(
        f"- [{n.uid}] {n.category}: {n.content[:120]}"
        for n in failed_nodes
    )

    simple_system = (
        "You are a data validator. For each node decide: is it factually plausible? "
        "Reply with valid JSON only."
    )
    agent = Agent(MODEL, system_prompt=simple_system, output_type=ValidationResult, retries=3)

    retry_results = []
    for node in failed_nodes:
        prompt = (
            f"Batch context (related nodes):\n{batch_context}\n\n"
            f"Validate this node:\n"
            f"category: {node.category}\n"
            f"content: {node.content}\n"
            f"source: {node.source}"
        )
        try:
            result = await agent.run(prompt)
            validation = result.output
        except Exception as e:
            validation = ValidationResult(
                is_valid=False,
                reason=f"Batch retry also failed: {type(e).__name__}",
                corrected_reliability=0.0,
            )

        if validation.is_valid:
            node.reliability = validation.corrected_reliability
            await store_node(db, node)
            retry_results.append({"uid": node.uid, "status": "stored_after_retry", "reliability": node.reliability})
        else:
            retry_results.append({"uid": node.uid, "status": "rejected", "reason": validation.reason})

    return retry_results


@router.post("/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest, req: Request):
    """
    Full pipeline: Ingest → Validate → Store → Discover edges
    Failed nodes are collected and batch-retried at the end with richer context.
    """
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    config = domain["config"]
    prompts = domain["prompts"]
    db = req.app.state.db

    # Step 1: Ingestor
    nodes = await ingest_raw_data(request.raw_text, config, prompts)

    stored_nodes: list[SovereignNode] = []
    failed_nodes: list[SovereignNode] = []
    results = []

    for node in nodes:
        # Step 2: Gatekeeper
        validation = await validate_node(node, config, prompts)

        if not validation.is_valid:
            failed_nodes.append(node)
            continue

        node.reliability = validation.corrected_reliability

        # Step 3: Store node
        await store_node(db, node)
        stored_nodes.append(node)
        results.append({"uid": node.uid, "status": "stored", "reliability": node.reliability})

    # Step 4: Architect — discover edges between stored nodes
    edges_created = 0
    if len(stored_nodes) > 1:
        existing_summary = "\n".join(
            f"[{n.uid}] {n.category}: {n.content[:100]}"
            for n in stored_nodes[:-1]
        )
        for node in stored_nodes:
            new_summary = f"[{node.uid}] {node.category}: {node.content}"
            suggestions: list[EdgeSuggestion] = await suggest_edges(
                new_summary, existing_summary, config, prompts
            )
            for s in suggestions:
                edge = SovereignEdge(
                    from_node=s.from_uid,
                    to_node=s.to_uid,
                    relation=s.relation,
                    weight=s.weight,
                )
                await store_edge(db, edge)
                edges_created += 1

    # Step 5: Batch retry for failed nodes (now with full context)
    retry_results = await _revalidate_failed(failed_nodes, config, prompts, db)
    results.extend(retry_results)

    return {
        "domain": domain_name,
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] in ("stored", "stored_after_retry")),
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
