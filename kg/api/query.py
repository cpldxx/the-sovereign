"""KG query route — semantic search over a domain, expanded one hop through the graph.

This is the read path agents use (Graph RAG): the closest nodes by embedding,
plus the edges touching them and the nodes on the other side of those edges.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import neighborhood, search_nodes
from core.embeddings import embed
from domains.registry import load_domain

router = APIRouter(prefix="/domains", tags=["query"])


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    k: int = Field(default=5, ge=1, le=50)


@router.post("/{domain_name}/query")
async def query_kg(domain_name: str, request: QueryRequest, req: Request):
    """
    Response shape:
    {
      "matches":   [{...node, "distance": 0.12}],   # closest first
      "neighbors": [{...node}],                     # 1 hop from matches
      "edges":     [{"from_node", "to_node", "relation", "weight"}]
    }
    """
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    db = req.app.state.db
    try:
        [vector] = await embed([request.query])
        matches = await search_nodes(db, domain_name, vector, request.k)
        neighbors, edges = await neighborhood(db, domain_name, [m["uid"] for m in matches])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"KG query failed: {type(e).__name__}: {e}")

    return {
        "domain": domain_name,
        "query": request.query,
        "matches": matches,
        "neighbors": neighbors,
        "edges": edges,
    }
