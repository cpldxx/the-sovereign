"""Ingest pipeline routes."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import kg
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["pipeline"])


class IngestRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)
    source: str = "user_input"
    title: str | None = None
    content_status: Literal["full", "partial"] = "full"
    # What kind of source (core/sources.py) — read from `source` when not given; it caps the facts' reliability.
    tier: Literal["paper", "official", "reference", "press", "user", "web", "blog"] | None = None


@router.post("/{domain_name}/ingest")
async def ingest(domain_name: str, request: IngestRequest, req: Request):
    """
    Store the text as an episode and grow the neuron graph from it:
    extract → ontology/text gates → validate → resolve entities → link facts → commit / Head review.
    Usually 2 LLM calls (+1 per ambiguity kind) and 1 embedding call per ~8k-char chunk.
    duplicate: the text was read already (pending: being read). failed + reason: a model call or a write failed —
    the episode is kept as failed and the same text is read again on the next ingest.
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await kg.ingest(
        req.app.state.db, domain_name, request.raw_text, request.source, request.title, request.content_status,
        request.tier,
    )

