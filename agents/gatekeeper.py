"""Gatekeeper - Validation agent for Blue Apple prevention.

All data must pass through this agent before entering the DB.
Uses Pydantic AI with LLM to verify logical consistency.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.schema import SovereignNode

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL", "ollama:llama3.1:8b")


class ValidationResult(BaseModel):
    """Typed validation output"""

    is_valid: bool
    reason: str = Field(..., description="Reason for pass/fail")
    corrected_reliability: float = Field(
        ..., ge=0.0, le=1.0, description="Adjusted reliability score"
    )


def create_gatekeeper(system_prompt: str) -> Agent:
    """Create a gatekeeper agent with domain-specific prompt"""
    return Agent(MODEL, system_prompt=system_prompt, output_type=ValidationResult, retries=5)


async def validate_node(node: SovereignNode, domain_config: dict, prompts_module) -> ValidationResult:
    """Validate a node before it enters the DB.

    Never raises — if the LLM fails after all retries, returns a safe reject
    so the pipeline keeps running and nothing enters the DB unvalidated.
    """
    name = domain_config["name"]
    description = domain_config["description"]

    agent = create_gatekeeper(prompts_module.gatekeeper_prompt(name, description))

    prompt = f"""Validate the following knowledge node:

Domain: {name}
Category: {node.category}
Content: {node.content}
Source: {node.source}
Current reliability: {node.reliability}

Determine if this information is logically valid and not misinformation."""

    try:
        result = await agent.run(prompt)
        return result.output
    except Exception as e:
        return ValidationResult(
            is_valid=False,
            reason=f"Gatekeeper failed after all retries: {type(e).__name__}",
            corrected_reliability=0.0,
        )
