"""KG operations — the logic behind both the REST API and the MCP tools.

    ingest  raw text → Ingestor → ontology check → Gatekeeper → embed → store → Architect → edges
    query   question → embed → vector search → 1-hop graph expansion
"""

from agents.architect import suggest_all_edges
from agents.gatekeeper import validate_nodes_batch
from agents.ingestor import ingest_raw_data
from core.database import ArcadeDB, neighborhood, search_nodes, store_edge, store_node
from core.embeddings import embed
from core.ontology import Ontology, normalize_type
from core.schema import SovereignEdge, SovereignNode
from domains.registry import load_domain


async def ingest(db: ArcadeDB, domain: str, raw_text: str, source: str = "user_input") -> dict:
    """Full pipeline, 3 LLM calls + 1 embedding call regardless of node count.

    Raises ValueError if the domain does not exist.
    """
    dom = load_domain(domain)
    config, prompts, ontology = dom["config"], dom["prompts"], dom["ontology"]
    grammar = Ontology.model_validate(ontology)

    # Step 1: Ingestor — 1 LLM call → all nodes
    nodes = await ingest_raw_data(raw_text, config, prompts, ontology, source)
    results = []

    # Ontology gate (code, not prompt): off-grammar categories never reach the KG.
    in_grammar: list[SovereignNode] = []
    for node in nodes:
        if node.category in grammar.entity_types:
            in_grammar.append(node)
        else:
            results.append({"uid": node.uid, "status": "rejected",
                            "reason": f"category '{node.category}' is not in the ontology"})

    # Step 2: Gatekeeper — 1 LLM call → validate all nodes at once
    validations = await validate_nodes_batch(in_grammar, config, prompts, ontology, raw_text)
    accepted = []
    for node, validation in zip(in_grammar, validations):
        if validation.is_valid:
            node.reliability = validation.corrected_reliability
            accepted.append(node)
        else:
            results.append({"uid": node.uid, "status": "rejected", "reason": validation.reason})

    # Embed all accepted nodes in one call. Without vectors the nodes are still
    # stored, just invisible to semantic search.
    try:
        vectors = dict(zip((n.uid for n in accepted), await embed([n.content for n in accepted])))
    except Exception as e:
        print(f"[KG] embedding failed, storing without vectors: {type(e).__name__}: {e}", flush=True)
        vectors = {}

    for node in accepted:
        await store_node(db, domain, node, vectors.get(node.uid))
        results.append({"uid": node.uid, "status": "stored", "reliability": node.reliability})

    # Step 3: Architect — 1 LLM call → all edges at once. Only in-grammar relations
    # between two distinct nodes of this batch are kept.
    edges_created = 0
    stored_uids = {n.uid for n in accepted}
    for s in await suggest_all_edges(accepted, config, prompts, ontology):
        relation = normalize_type(s.relation)
        if (
            relation not in grammar.relation_types
            or s.from_uid == s.to_uid
            or not {s.from_uid, s.to_uid} <= stored_uids
        ):
            continue
        edge = SovereignEdge(from_node=s.from_uid, to_node=s.to_uid, relation=relation, weight=s.weight)
        if await store_edge(db, domain, edge):
            edges_created += 1

    return {
        "domain": domain,
        "total": len(nodes),
        "stored": len(accepted),
        "rejected": len(nodes) - len(accepted),
        "edges_created": edges_created,
        "embedded": sum(1 for n in accepted if n.uid in vectors),
        "details": results,
    }


async def query(db: ArcadeDB, domain: str, question: str, k: int = 5) -> dict:
    """Graph RAG read: the k closest nodes by embedding, plus their 1-hop neighborhood.

    Raises ValueError if the domain does not exist.
    """
    load_domain(domain)
    [vector] = await embed([question])
    matches = await search_nodes(db, domain, vector, k)
    neighbors, edges = await neighborhood(db, domain, [m["uid"] for m in matches])
    return {
        "domain": domain,
        "query": question,
        "matches": matches,
        "neighbors": neighbors,
        "edges": edges,
    }
