"""Architect - Graph builder agent.

Discovers relationships between all nodes in a single LLM call.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL_ARCHITECT", "ollama:qwen2.5:7b")


class EdgeSuggestion(BaseModel):
    from_uid: str
    to_uid: str
    relation: str
    weight: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this relationship exists")


class EdgeList(BaseModel):
    edges: list[EdgeSuggestion]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"edges": v}
        if isinstance(v, dict) and "arguments" in v:
            args = v["arguments"]
            if isinstance(args, list):
                return {"edges": args}
            return args
        return v


async def suggest_all_edges(
    nodes: list,
    domain_config: dict,
    prompts_module,
    ontology: dict | None = None,
) -> list[EdgeSuggestion]:
    """Discover all relationships between nodes in a single LLM call.

    Never raises — returns empty list on failure.
    """
    if len(nodes) < 2:
        return []

    name = domain_config["name"]
    description = domain_config["description"]

    agent = Agent(MODEL, system_prompt=prompts_module.architect_prompt(name, description, ontology), output_type=EdgeList)

    nodes_text = "\n".join(
        f"[{n.uid}] {n.category}: {n.content}"
        for n in nodes
    )

    prompt = f"""Discover all meaningful relationships between these nodes in the {name} domain.

Nodes:
{nodes_text}

Suggest edges between any pairs that have a meaningful relationship."""

    try:
        result = await agent.run(prompt)
        return result.output.edges
    except Exception:
        return []
