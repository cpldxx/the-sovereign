"""Head Agent routes — talk to the KG-aware Head Agent for a domain."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.head_agent import ask_head
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["agent"])


class AskRequest(BaseModel):
    message: str
    max_iterations: int = 8


@router.post("/{domain_name}/ask")
async def ask(domain_name: str, request: AskRequest):
    """Ask the Head Agent a question. It grounds itself in the KG before answering.

    Hermes's orchestration loop is synchronous, so we run it in a worker thread to
    avoid blocking the event loop.
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        answer = await asyncio.to_thread(
            ask_head, domain_name, request.message, max_iterations=request.max_iterations
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Head Agent failed: {type(e).__name__}: {e}")

    return {"domain": domain_name, "message": request.message, "answer": answer}
