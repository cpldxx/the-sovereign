"""KG tools — bridges the Sovereign knowledge graph into the Hermes tool registry.

These are the tools the Head Agent (Hermes AIAgent) uses to read from and write
to the knowledge graph. They are registered into Hermes's global tool registry,
scoped to a single domain via closure binding.

Hermes handler convention (verified): handlers are dispatched as
``handler(args: dict, **kwargs)`` where ``args`` is the parsed tool-call argument
dict and kwargs carries Hermes context (task_id, session_id, user_task). They must
return a JSON string — use ``tool_result`` / ``tool_error``.
"""

from tools.registry import registry, tool_error, tool_result

from agents.architect import suggest_all_edges
from agents.gatekeeper import validate_nodes_batch
from agents.ingestor import ingest_raw_data
from core.database import get_db, query_edges, query_nodes, store_edge, store_node
from core.schema import SovereignEdge
from domains.registry import load_domain

KG_TOOLSET = "knowledge_graph"


def _normalize(raw) -> list[dict]:
    """SurrealDB returns a plain list of dicts."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def register_kg_tools(domain: str) -> None:
    """Register query_knowledge_graph + ingest_data, bound to `domain`.

    Re-registers with override=True so each Head Agent run rebinds to its domain.
    Safe for the current sequential-per-task execution model; if domains ever run
    concurrently this global mutation needs per-agent isolation.
    """

    # ── query_knowledge_graph ──────────────────────────────────────────────
    async def _query_kg(args, **kwargs) -> str:
        query = args.get("query", "") if isinstance(args, dict) else str(args)
        db = await get_db()
        try:
            nodes = _normalize(await query_nodes(db, domain=domain))
            edges = _normalize(await query_edges(db, domain=domain))
        finally:
            await db.close()

        # Lightweight relevance filter: if the agent gave search terms, prefer
        # nodes that mention them; otherwise return everything (domains are small).
        terms = [t.lower() for t in query.split() if len(t) > 2]
        if terms:
            def _hit(n: dict) -> bool:
                hay = f"{n.get('content','')} {n.get('category','')} {' '.join(n.get('tags',[]))}".lower()
                return any(t in hay for t in terms)
            matched = [n for n in nodes if _hit(n)]
            nodes_out = matched or nodes  # fall back to all if nothing matched
        else:
            nodes_out = nodes

        return tool_result(data={
            "domain": domain,
            "query": query,
            "node_count": len(nodes_out),
            "edge_count": len(edges),
            "nodes": [
                {
                    "uid": n.get("uid"),
                    "category": n.get("category"),
                    "content": n.get("content"),
                    "reliability": n.get("reliability"),
                    "tags": n.get("tags", []),
                }
                for n in nodes_out
            ],
            "edges": [
                {"from": e.get("from_node"), "to": e.get("to_node"),
                 "relation": e.get("relation"), "weight": e.get("weight")}
                for e in edges
            ],
        })

    # ── ingest_data ────────────────────────────────────────────────────────
    async def _ingest_data(args, **kwargs) -> str:
        if not isinstance(args, dict):
            return tool_error("ingest_data requires {raw_text, source?}")
        raw_text = args.get("raw_text", "")
        source = args.get("source", "head_agent")
        if not raw_text.strip():
            return tool_error("raw_text is empty")

        try:
            dom = load_domain(domain)
        except ValueError as e:
            return tool_error(str(e))
        config, prompts, ontology = dom["config"], dom["prompts"], dom.get("ontology")

        nodes = await ingest_raw_data(raw_text, config, prompts, ontology)
        if not nodes:
            return tool_result(data={"stored": 0, "rejected": 0, "edges_created": 0,
                                     "note": "Ingestor extracted no nodes."})

        validations = await validate_nodes_batch(nodes, config, prompts, ontology)

        db = await get_db()
        try:
            stored = []
            rejected = []
            for node, v in zip(nodes, validations):
                if not v.is_valid:
                    rejected.append({"uid": node.uid, "reason": v.reason})
                    continue
                node.reliability = v.corrected_reliability
                await store_node(db, node)
                stored.append(node)

            edges_created = 0
            if len(stored) > 1:
                for s in await suggest_all_edges(stored, config, prompts, ontology):
                    await store_edge(db, SovereignEdge(
                        from_node=s.from_uid, to_node=s.to_uid,
                        relation=s.relation, weight=s.weight,
                    ))
                    edges_created += 1
        finally:
            await db.close()

        return tool_result(data={
            "stored": len(stored),
            "rejected": len(rejected),
            "edges_created": edges_created,
            "stored_uids": [n.uid for n in stored],
            "rejections": rejected,
        })

    registry.register(
        name="query_knowledge_graph",
        toolset=KG_TOOLSET,
        schema={"type": "function", "function": {
            "name": "query_knowledge_graph",
            "description": (
                "Read the Sovereign knowledge graph for the current domain. "
                "Returns stored knowledge nodes and their relationships. "
                "Use this to ground answers in accumulated, validated knowledge."
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string",
                          "description": "Search terms / topic to look up. Empty returns everything."},
            }, "required": ["query"]},
        }},
        handler=_query_kg,
        is_async=True,
        description="Read the Sovereign knowledge graph",
        override=True,
    )

    registry.register(
        name="ingest_data",
        toolset=KG_TOOLSET,
        schema={"type": "function", "function": {
            "name": "ingest_data",
            "description": (
                "Add new raw knowledge to the Sovereign knowledge graph. Runs the full "
                "pipeline: extract nodes (ontology-constrained) -> validate -> store -> "
                "discover edges. Use when you have new factual information worth persisting."
            ),
            "parameters": {"type": "object", "properties": {
                "raw_text": {"type": "string", "description": "The raw knowledge text to ingest."},
                "source": {"type": "string", "description": "Where it came from (URL, paper, API)."},
            }, "required": ["raw_text"]},
        }},
        handler=_ingest_data,
        is_async=True,
        description="Write new knowledge into the Sovereign knowledge graph",
        override=True,
    )
