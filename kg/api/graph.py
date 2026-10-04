"""Graph API — serves nodes + edges as JSON for visualization."""

from fastapi import APIRouter, HTTPException, Request

from core.database import query_edges, query_nodes
from domains.registry import list_domains

router = APIRouter(prefix="/graph", tags=["graph"])


def _viz_node(n: dict) -> dict:
    return {
        "id": n.get("uid", ""),
        "label": n.get("content", "")[:80],
        "category": n.get("category", ""),
        "domain": n.get("domain", ""),
        "source": n.get("source", ""),
        "tags": n.get("tags", []),
        "reliability": n.get("reliability", 0.0),
    }


def _viz_edge(e: dict) -> dict:
    return {
        "from": e.get("from_node", ""),
        "to": e.get("to_node", ""),
        "relation": e.get("relation", ""),
        "weight": e.get("weight", 1.0),
    }


async def _domain_graph(db, domain: str) -> tuple[list[dict], list[dict]]:
    try:
        nodes = await query_nodes(db, domain)
        edges = await query_edges(db, domain)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB query failed: {e}")
    return [_viz_node(n) for n in nodes], [_viz_edge(e) for e in edges]


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
    nodes, edges = await _domain_graph(req.app.state.db, domain_name)
    return {
        "domain": domain_name,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


@router.get("")
async def get_all_graphs(req: Request):
    """Returns nodes + edges for ALL domains (one database per domain)."""
    nodes, edges = [], []
    for domain in list_domains():
        d_nodes, d_edges = await _domain_graph(req.app.state.db, domain)
        nodes += d_nodes
        edges += d_edges
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
