"""Graph API — serves nodes + edges as JSON for visualization."""

from fastapi import APIRouter, HTTPException, Request

from core.database import query_edges, query_nodes

router = APIRouter(prefix="/graph", tags=["graph"])


def _extract_results(raw) -> list[dict]:
    """SurrealDB returns a plain list of dicts directly."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


@router.get("/{domain_name}")
async def get_graph(domain_name: str, req: Request):
    """
    Returns nodes + edges for a domain, ready for graph visualization.

    Response shape:
    {
      "nodes": [{"id": "uid", "label": "...", "category": "...", "reliability": 0.9, ...}],
      "edges": [{"from": "uid1", "to": "uid2", "relation": "...", "weight": 0.8}]
    }
    """
    db = req.app.state.db

    try:
        raw_nodes = await query_nodes(db, domain=domain_name)
        raw_edges = await query_edges(db, domain=domain_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB query failed: {e}")

    nodes = _extract_results(raw_nodes)
    edges = _extract_results(raw_edges)

    # Normalize nodes for visualization
    viz_nodes = [
        {
            "id": n.get("uid", str(n.get("id", ""))),
            "label": n.get("content", "")[:80],
            "category": n.get("category", ""),
            "domain": n.get("domain", ""),
            "source": n.get("source", ""),
            "tags": n.get("tags", []),
            "reliability": n.get("reliability", 0.0),
        }
        for n in nodes
        if isinstance(n, dict)
    ]

    # Normalize edges for visualization
    viz_edges = [
        {
            "from": e.get("from_node", ""),
            "to": e.get("to_node", ""),
            "relation": e.get("relation", ""),
            "weight": e.get("weight", 1.0),
        }
        for e in edges
        if isinstance(e, dict)
    ]

    return {
        "domain": domain_name,
        "node_count": len(viz_nodes),
        "edge_count": len(viz_edges),
        "nodes": viz_nodes,
        "edges": viz_edges,
    }


@router.get("")
async def get_all_graphs(req: Request):
    """Returns nodes + edges for ALL domains."""
    db = req.app.state.db

    try:
        raw_nodes = await query_nodes(db)
        raw_edges = await query_edges(db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB query failed: {e}")

    nodes = _extract_results(raw_nodes)
    edges = _extract_results(raw_edges)

    viz_nodes = [
        {
            "id": n.get("uid", str(n.get("id", ""))),
            "label": n.get("content", "")[:80],
            "category": n.get("category", ""),
            "domain": n.get("domain", ""),
            "reliability": n.get("reliability", 0.0),
        }
        for n in nodes
        if isinstance(n, dict)
    ]

    viz_edges = [
        {
            "from": e.get("from_node", ""),
            "to": e.get("to_node", ""),
            "relation": e.get("relation", ""),
            "weight": e.get("weight", 1.0),
        }
        for e in edges
        if isinstance(e, dict)
    ]

    return {
        "node_count": len(viz_nodes),
        "edge_count": len(viz_edges),
        "nodes": viz_nodes,
        "edges": viz_edges,
    }
