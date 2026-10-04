"""Ontologist - Domain ontology generator.

Generates entity_types and relation_types from domain name + description.
Called once at domain creation; result saved as ontology.json.
"""

import os

from dotenv import load_dotenv
from pydantic_ai import Agent

from core.ontology import Ontology, save_ontology
from domains.registry import ontology_path

load_dotenv()

MODEL = os.getenv("OLLAMA_MODEL_ONTOLOGIST", "ollama:qwen2.5:32b")

SYSTEM_PROMPT = """You are The Sovereign's Ontologist. Your job is to define the grammar of a knowledge graph.

Given a domain name and description, generate:
- entity_types: 5-8 specific node categories for this domain (lowercase_underscore)
- relation_types: 4-6 edge types describing how entities connect (lowercase_underscore)

Rules:
- Be domain-specific, not generic (avoid "concept", "thing", "item")
- Types must be mutually exclusive and cover the domain comprehensively
- relation_types should be directional verbs or verb phrases"""


async def generate_ontology(domain_name: str, description: str) -> Ontology:
    agent = Agent(MODEL, system_prompt=SYSTEM_PROMPT, output_type=Ontology, retries=3)
    result = await agent.run(
        f"Domain: {domain_name}\nDescription: {description}\n\nGenerate the ontology."
    )
    return result.output


async def bootstrap_domain_ontology(domain: str, description: str, *, overwrite: bool = False) -> None:
    """Generate and save ontology.json for a domain. Never raises.

    Slow (minutes on a large local model), so it runs as a background task. On
    failure nothing is written: the domain keeps its current ontology (or the
    default) and generation can be retried via POST /domains/{domain}/ontology/generate.
    """
    try:
        if ontology_path(domain).exists() and not overwrite:
            return
        ontology = await generate_ontology(domain, description)
        save_ontology(domain, ontology)
    except Exception as e:
        print(f"[Ontologist] Failed for '{domain}': {type(e).__name__}: {e}", flush=True)
        return
    print(f"[Ontologist] '{domain}' → {ontology.entity_types}", flush=True)
