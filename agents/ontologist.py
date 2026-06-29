"""Ontologist - Domain ontology generator.

Generates entity_types and relation_types from domain name + description.
Called once at domain creation; result saved as ontology.json.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL_HEAD", "ollama:qwen2.5:32b")

SYSTEM_PROMPT = """You are The Sovereign's Ontologist. Your job is to define the grammar of a knowledge graph.

Given a domain name and description, generate:
- entity_types: 5-8 specific node categories for this domain (lowercase_underscore)
- relation_types: 4-6 edge types describing how entities connect (lowercase_underscore)

Rules:
- Be domain-specific, not generic (avoid "concept", "thing", "item")
- Types must be mutually exclusive and cover the domain comprehensively
- relation_types should be directional verbs or verb phrases"""


class Ontology(BaseModel):
    entity_types: list[str]
    relation_types: list[str]


DEFAULT_ONTOLOGY = {
    "entity_types": ["concept", "method", "tool", "finding", "event", "metric", "entity"],
    "relation_types": ["supports", "contradicts", "derived_from", "related_to", "precedes", "enables"],
}


async def generate_ontology(domain_name: str, description: str) -> Ontology:
    agent = Agent(MODEL, system_prompt=SYSTEM_PROMPT, output_type=Ontology, retries=3)
    result = await agent.run(
        f"Domain: {domain_name}\nDescription: {description}\n\nGenerate the ontology."
    )
    return result.output


async def bootstrap_domain_ontology(domain_dir: Path, domain_name: str, description: str) -> None:
    """Generate and save ontology.json for a domain. Never raises."""
    ontology_path = domain_dir / "ontology.json"
    if ontology_path.exists():
        return
    try:
        ontology = await generate_ontology(domain_name, description)
        ontology_path.write_text(json.dumps(ontology.model_dump(), indent=2))
        print(f"[Ontologist] '{domain_name}' → {ontology.entity_types}")
    except Exception as e:
        print(f"[Ontologist] Failed for '{domain_name}': {e} — using default")
        ontology_path.write_text(json.dumps(DEFAULT_ONTOLOGY, indent=2))
