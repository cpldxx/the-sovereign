"""Gatekeeper - Validation agent for Blue Apple prevention.

All data must pass through this agent before entering the DB.
Batch validates all nodes in a single LLM call.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from core.schema import SovereignNode

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL_GATEKEEPER", "ollama:qwen2.5:32b")


class NodeValidation(BaseModel):
    uid: str
    is_valid: bool
    reason: str
    corrected_reliability: float = Field(ge=0.0, le=1.0)


class BatchValidationResult(BaseModel):
    results: list[NodeValidation]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"results": v}
        if isinstance(v, dict) and "arguments" in v:
            args = v["arguments"]
            if isinstance(args, list):
                return {"results": args}
            return args
        return v


async def validate_nodes_batch(
    nodes: list[SovereignNode],
    domain_config: dict,
    prompts_module,
    ontology: dict | None = None,
    source_text: str = "",
) -> list[NodeValidation]:
    """Validate all nodes in a single LLM call.

    Returns NodeValidation list in same order as input.
    Never raises — failed nodes get is_valid=False.
    """
    if not nodes:
        return []

    name = domain_config["name"]
    description = domain_config["description"]

    agent = Agent(MODEL, system_prompt=prompts_module.gatekeeper_prompt(name, description, ontology), output_type=BatchValidationResult, retries=5)

    nodes_text = "\n\n".join(
        f"[{i+1}] uid: {n.uid}\n  category: {n.category}\n  content: {n.content}\n  source: {n.source}\n  reliability: {n.reliability}"
        for i, n in enumerate(nodes)
    )

    prompt = f"""Validate ALL of the following knowledge nodes for the {name} domain.
Return one validation result per node, using each node's exact uid.

Source text the nodes were extracted from:
\"\"\"
{source_text}
\"\"\"

Nodes to validate:
{nodes_text}"""

    try:
        result = await agent.run(prompt)
        validations = {v.uid: v for v in result.output.results}
        # Return in same order as input, fallback reject if uid missing
        return [
            validations.get(n.uid, NodeValidation(
                uid=n.uid,
                is_valid=False,
                reason="Not returned by Gatekeeper",
                corrected_reliability=0.0,
            ))
            for n in nodes
        ]
    except Exception as e:
        return [
            NodeValidation(
                uid=n.uid,
                is_valid=False,
                reason=f"Gatekeeper batch failed: {type(e).__name__}",
                corrected_reliability=0.0,
            )
            for n in nodes
        ]
