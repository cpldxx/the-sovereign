"""Architect - Graph builder agent.

Stores validated nodes in SurrealDB and discovers relationships.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL", "ollama:llama3.1:8b")


class EdgeSuggestion(BaseModel):
    """Agent-suggested relationship between nodes"""

    from_uid: str
    to_uid: str
    relation: str
    weight: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this relationship exists")


def create_architect(system_prompt: str) -> Agent:
    """Create an architect agent with domain-specific prompt"""
    return Agent(MODEL, system_prompt=system_prompt, output_type=list[EdgeSuggestion])


async def suggest_edges(
    new_node_summary: str,
    existing_nodes_summary: str,
    domain_config: dict,
    prompts_module,
) -> list[EdgeSuggestion]:
    """Suggest relationships between the new node and existing nodes.

    Never raises — returns empty list on failure so the pipeline keeps running.
    """
    name = domain_config["name"]
    description = domain_config["description"]

    agent = create_architect(prompts_module.architect_prompt(name, description))

    prompt = f"""Newly added node:
{new_node_summary}

Existing nodes in {name} domain:
{existing_nodes_summary}

Suggest meaningful relationships between these nodes."""

    try:
        result = await agent.run(prompt)
        return result.output
    except Exception:
        return []
