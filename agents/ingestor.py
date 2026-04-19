"""Ingestor - Data collection agent.

Extracts knowledge from raw data and converts it into SovereignNode format.
"""

from pydantic_ai import Agent

from core.schema import SovereignNode

ingestor = Agent(
    # TODO: Replace with Ollama local model
    "openai:gpt-4o-mini",
    system_prompt="""You are The Sovereign's Ingestor.

Your role:
1. Analyze raw input data
2. Extract key knowledge
3. Format it into SovereignNode schema

Rules:
- Multiple nodes can be extracted from a single input
- Each node must contain exactly one clear fact
- Source must always be specified
- Reliability is scored based on source authority and verifiability""",
    output_type=list[SovereignNode],
)


async def ingest_raw_data(raw_text: str, domain: str, source: str) -> list[SovereignNode]:
    """Convert raw data into a list of SovereignNodes"""
    prompt = f"""Extract knowledge nodes from the following raw data:

Domain: {domain}
Source: {source}
Data:
{raw_text}

Format each node's uid as '{domain}:extracted_category:serial_number'."""

    result = await ingestor.run(prompt)
    return result.output
