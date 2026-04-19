"""Gatekeeper - Validation agent for Blue Apple prevention.

All data must pass through this agent before entering the DB.
Uses Pydantic AI with LLM to verify logical consistency.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.schema import SovereignNode


class ValidationResult(BaseModel):
    """Typed validation output"""

    is_valid: bool
    reason: str = Field(..., description="Reason for pass/fail")
    corrected_reliability: float = Field(
        ..., ge=0.0, le=1.0, description="Adjusted reliability score"
    )


gatekeeper = Agent(
    # TODO: Replace with Ollama local model (e.g. 'ollama:nemotron')
    "openai:gpt-4o-mini",
    system_prompt="""You are The Sovereign's Gatekeeper.

Your role:
1. Verify that incoming data is logically sound
2. Block false information (e.g. "blue apples exist naturally")
3. Adjust reliability scores based on evidence

Criteria:
- Is the source credible?
- Does the content align with known facts in its domain?
- Are there any self-contradictions?

Be strict. When in doubt, reject.""",
    output_type=ValidationResult,
)


async def validate_node(node: SovereignNode) -> ValidationResult:
    """Validate a node before it enters the DB"""
    prompt = f"""Validate the following knowledge node:

Domain: {node.domain}
Category: {node.category}
Content: {node.content}
Source: {node.source}
Current reliability: {node.reliability}

Determine if this information is logically valid and not misinformation."""

    result = await gatekeeper.run(prompt)
    return result.output
