"""Ontologist - Domain ontology generator.

Generates entity_types and relation_types from domain name + description.
Called once at domain creation; result saved as ontology.json.
"""


from pydantic_ai import Agent

from core.llm import model_for, model_settings
from core.ontology import Ontology, save_ontology
from domains.registry import ontology_path


MODEL = model_for("ontologist")

SYSTEM_PROMPT = """You are The Sovereign's Ontologist. Your job is to define the grammar of a knowledge graph.

The graph's nodes are ENTITIES — discrete, nameable things (companies, products, technologies, people,
policies, indicators, places, events). Its edges are FACTS — one entity acting on or relating to another.

Given a domain name and description, generate:
- entity_types: 5-8 kinds of entity for this domain (lowercase_underscore), e.g. "company", "chip_product",
  "central_bank". Kinds of THINGS — never kinds of statements ("finding", "trend", "risk_signal", "claim").
- relation_types: 5-8 directional verb phrases between entities (lowercase_underscore), e.g. "supplies",
  "competes_with", "regulates", "depends_on", "invests_in".

Rules:
- Domain-specific, not generic (avoid "concept", "thing", "item", "related_to").
- Entity types mutually exclusive and together covering the domain.
- Facts about a single entity (its status or numbers) use a built-in relation; do not invent one for them.
- When SOURCE EXCERPTS are given, derive the types from what those sources actually talk about — the kinds of
  things they name and the ways they relate them — rather than from what the domain might contain."""

EXCERPT_CHARS = 1500   # per source
MAX_EXCERPTS = 10


async def generate_ontology(domain_name: str, description: str, corpus: list[str] | None = None) -> Ontology:
    """`corpus`: texts from real sources (research) to ground the grammar in; without it the
    ontology comes from the description alone."""
    agent = Agent(MODEL, name="ontologist", model_settings=model_settings(), system_prompt=SYSTEM_PROMPT,
                  output_type=Ontology, retries=3)
    prompt = f"Domain: {domain_name}\nDescription: {description}\n"
    if corpus:
        excerpts = "\n\n".join(
            f"[{i}] {text.strip()[:EXCERPT_CHARS]}" for i, text in enumerate(corpus[:MAX_EXCERPTS]) if text.strip()
        )
        prompt += f"\nSOURCE EXCERPTS (from real research on this domain):\n{excerpts}\n"
    result = await agent.run(prompt + "\nGenerate the ontology.")
    return result.output


async def bootstrap_domain_ontology(
    domain: str, description: str, *, overwrite: bool = False, corpus: list[str] | None = None
) -> None:
    """Generate and save ontology.json for a domain. Never raises.

    Slow (minutes on a large local model), so it runs as a background task. On
    failure nothing is written: the domain keeps its current ontology (or the
    default) and generation can be retried via POST /domains/{domain}/ontology/generate.
    """
    try:
        if ontology_path(domain).exists() and not overwrite:
            return
        ontology = await generate_ontology(domain, description, corpus)
        # Generation takes a while: an ontology saved meanwhile (by the user, the Head or the research
        # bootstrap's corpus-grounded one) wins over this description-only one.
        if ontology_path(domain).exists() and not overwrite:
            return
        save_ontology(domain, ontology)
    except Exception as e:
        print(f"[Ontologist] Failed for '{domain}': {type(e).__name__}: {e}", flush=True)
        return
    print(f"[Ontologist] '{domain}' → {ontology.entity_types}", flush=True)
