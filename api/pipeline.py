"""Ingest pipeline routes."""

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


@router.post("/{domain_name}/generate-tools")
async def generate_tools(domain_name: str):
    """
    Spawn an ephemeral OpenHands container to generate domain-specific tools.py.
    Overwrites domains/{domain_name}/tools.py with the AI-generated implementation.
    """
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    config = domain["config"]
    tools_path = DOMAINS_DIR / domain_name / "tools.py"

    if not tools_path.parent.exists():
        raise HTTPException(status_code=404, detail=f"Domain directory not found: {domain_name}")

    try:
        generated_code = await generate_domain_tools(domain_name, config)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    tools_path.write_text(generated_code)

    return {
        "domain": domain_name,
        "status": "generated",
        "path": str(tools_path),
        "lines": generated_code.count("\n") + 1,
    }
