"""Ingestor - Data collection agent.

Extracts knowledge from raw data and converts it into SovereignNode format.
Uses domain-specific prompts for better extraction.
"""

import os

from dotenv import load_dotenv
from pydantic import BaseModel, model_validator
from pydantic_ai import Agent

from core.schema import SovereignNode

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL_INGESTOR", "ollama:qwen2.5:7b")

class NodeList(BaseModel):
    """Accepts both {"nodes": [...]} and plain [...] from LLM."""
    nodes: list[SovereignNode]

    @model_validator(mode="before")
    @classmethod
    def wrap_if_list(cls, v):
        if isinstance(v, list):
            return {"nodes": v}
        # Pydantic AI tool call format: {"name": "final_result", "arguments": {...}}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


def create_ingestor(system_prompt: str) -> Agent:
    """Create an ingestor agent with domain-specific prompt"""
    return Agent(MODEL, system_prompt=system_prompt, output_type=NodeList, retries=5)


async def ingest_raw_data(raw_text: str, domain_config: dict, prompts_module, ontology: dict | None = None) -> list[SovereignNode]:
    """Convert raw data into a list of SovereignNodes"""
    name = domain_config["name"]
    description = domain_config["description"]

    agent = create_ingestor(prompts_module.ingestor_prompt(name, description, ontology))

    prompt = f"""Extract knowledge nodes from the following raw data:

Domain: {name}
Source: provided input
Data:
{raw_text}

Format each node's uid as '{name}:extracted_category:serial_number'."""

    try:
        result = await agent.run(prompt)
        return result.output.nodes
    except Exception as e:
        import traceback
        print(f"[Ingestor] failed: {type(e).__name__}: {e}")
        print(f"[Ingestor] full cause: {getattr(e, '__cause__', None)}")
        traceback.print_exc()
        return []
