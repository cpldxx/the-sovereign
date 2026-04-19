"""Architect - Graph builder agent.

Stores validated nodes in SurrealDB and discovers cross-domain relationships.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.schema import SovereignEdge


class EdgeSuggestion(BaseModel):
    """Agent-suggested relationship between nodes"""

    from_uid: str
    to_uid: str
    relation: str
    weight: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this relationship exists")


architect = Agent(
    # TODO: Replace with Ollama local model
    "openai:gpt-4o-mini",
    system_prompt="""You are The Sovereign's Architect.

Your role:
1. Discover relationships between new and existing nodes
2. Find cross-domain bridges (the most valuable connections)
3. Assess relationship strength (weight)

Key cross-domain patterns to look for:
- Quant optimization algorithms <-> Academic math theory
- Artisanal aesthetic patterns <-> Quant chart patterns
- Academic statistical models <-> Artisanal recipe optimization

You exist to find connections that humans cannot see.""",
    output_type=list[EdgeSuggestion],
)


async def suggest_edges(
    new_node_summary: str, existing_nodes_summary: str
) -> list[EdgeSuggestion]:
    """Suggest relationships between new and existing nodes"""
    prompt = f"""Newly added node:
{new_node_summary}

Existing nodes:
{existing_nodes_summary}

Suggest meaningful relationships between these nodes.
Prioritize cross-domain connections."""

    result = await architect.run(prompt)
    return result.output
