"""KG query route — Graph RAG over a domain's neuron graph.

This is the read path agents use: the entities and facts closest to the question by
embedding, the strongest valid facts around them, and the sources behind those facts.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import kg
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["query"])


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    k: int = Field(default=5, ge=1, le=50)


@router.post("/{domain_name}/query")
async def query_kg(domain_name: str, request: QueryRequest, req: Request):
    """
    {
      "entities": [{...entity, "similarity"?}],      # closest entities (with similarity) + fact endpoints
      "facts":    [{...fact, "similarity"?}],         # closest facts first, then strongest facts around them
      "episodes": [{"uid", "source", "title", ...}]   # sources behind the facts
    }
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await kg.query(req.app.state.db, domain_name, request.query, request.k)
