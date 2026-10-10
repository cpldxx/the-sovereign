"""Extractor — turns source text into entities (neurons) and facts (synapses) in one LLM call.

Entity and fact rules adapted from Graphiti's combined extraction prompt
(github.com/getzep/graphiti, Apache 2.0) for documents instead of conversations.
"""

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings
from core.ontology import Ontology

MODEL = model_for("extractor")
STATE = "has_state"


class ExtractedEntity(BaseModel):
    name: str = Field(description="Most specific name used in the text, at most 5 words")
    type: str = Field(description="One of the ENTITY TYPES")
    aliases: list[str] = Field(default_factory=list, description="Other names for the same thing in the text")
    description: str = Field(default="", description="One short sentence: what this entity is, from the text")


class ExtractedFact(BaseModel):
    source: str = Field(description="Name of an extracted entity")
    relation: str = Field(description=f"One of the RELATION TYPES, or {STATE}")
    target: str = Field(description=f"Name of an extracted entity (same as source for {STATE})")
    fact: str = Field(description="One self-contained sentence stating the fact, with all numbers and dates")


class Extraction(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        # Some models wrap tool output as {"name": ..., "arguments": {...}}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Extractor for the "{domain}" domain: {description}
You turn source text into a knowledge graph of ENTITIES (things) and FACTS (relationships between things).
The source text will NOT be available later — only what you extract survives.

ENTITY RULES
1. An entity is a discrete, nameable thing: an organization, person, product, technology, place, policy,
   indicator, event, etc. Classify each with exactly ONE of the ENTITY TYPES. Skip things that fit no type.
2. name: the most specific form used in the text, at most 5 words — the thing itself, not a quantity, stage or
   shipment of it ("HBM4", not "HBM4 samples"; "Blackwell", not "Blackwell orders"). aliases: other names the text uses for the
   SAME thing (abbreviation, full name, spelling) — never a group it belongs to ("hyperscalers"), a product or unit
   of it ("Maia 200" is not Microsoft), a parent or a competitor. description: one short sentence on what it is,
   from the text only.
3. NOT entities: pronouns and unresolved references ("the company"); vague abstractions (growth, demand,
   supply, risk, market); full sentences; numbers, amounts, prices, percentages, dates and durations —
   those belong inside fact text.
4. Extract EVERY organization, product and technology the text names — including each one in a list
   ("Nvidia and AMD" → two entities).
5. Each real-world thing appears ONCE. When the text uses several names for it ("CoWoS", "CoWoS packaging",
   "CoWoS advanced packaging") list one entity and put the other names in aliases. Variants and versions are
   different things — keep the exact variant the text names (CoWoS vs CoWoS-L, HBM3 vs HBM3E).

FACT RULES
1. source and target must be names from your entities list.
2. relation: one of the RELATION TYPES, spelled exactly as listed. A fact that involves TWO of your entities MUST
   connect them with the closest RELATION TYPE — this is what links the graph together. When only the reverse
   relation is listed, swap source and target ("CoWoS is made by TSMC" with "manufactures" listed → TSMC
   manufactures CoWoS). Use "{state}" (target = source) ONLY for a fact about one entity with no second listed
   entity in it.
3. fact: ONE self-contained sentence, understandable without the source, keeping every number, date,
   variant name and qualifier ("expected", "plans to", "about"). State only what the text says — no inference,
   no outside knowledge. Every fact in the text becomes one fact; do not merge two facts into one."""


def _agent(domain: str, description: str) -> Agent:
    return Agent(
        MODEL,
        name="extractor",
        model_settings=model_settings(),
        system_prompt=SYSTEM.format(domain=domain, description=description, state=STATE),
        output_type=Extraction,
        retries=3,
    )


async def extract(text: str, domain: str, description: str, ontology: Ontology, source: str) -> Extraction:
    """One LLM call → all entities and facts of the text. Never raises (empty on failure)."""
    prompt = (
        f"ENTITY TYPES: {', '.join(ontology.entity_types)}\n"
        f"RELATION TYPES: {', '.join(ontology.relation_types)}, {STATE}\n\n"
        f"SOURCE ({source}):\n\"\"\"\n{text}\n\"\"\""
    )
    try:
        return (await _agent(domain, description).run(prompt)).output
    except Exception as e:
        print(f"[Extractor] failed: {type(e).__name__}: {e}", flush=True)
        return Extraction()


# ── Relations hidden in facts about one entity ─────────────────────────────
# A local model files some relations as has_state: "Samsung is finalizing quality testing for its HBM3E chips with
# Nvidia" stored as a state of HBM3E links nothing. Measured 2026-10-10: 73 of the 157 state facts of ai_chips'
# unconnected entities named another entity of the graph. One more call per batch asks what those statements relate.

class Relation(BaseModel):
    index: int = Field(description="Number of the statement")
    source: str = Field(description="Name from that statement's ENTITIES")
    relation: str = Field(description="One of the RELATION TYPES")
    target: str = Field(description="Name from that statement's ENTITIES")


class Relations(BaseModel):
    relations: list[Relation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"relations": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


RELATE = """You read statements from a knowledge graph's sources. Each was stored as a fact about one entity, but it
also names other entities. For each statement, list the relations it STATES between its ENTITIES, using only the
RELATION TYPES (when only the reverse relation is listed, swap source and target).

- Only what the sentence itself says, nothing inferred: "Samsung is testing its HBM3E chips with Nvidia" states that
  Samsung makes HBM3E; that Samsung supplies Nvidia only if a supply relation is stated.
- source and target are names from that statement's ENTITIES list, spelled as listed, and different.
- No relation type fits → nothing for that statement. Most statements give zero, one or two relations."""

_relate = Agent(MODEL, name="relate", model_settings=model_settings(decisive=True), system_prompt=RELATE,
                output_type=Relations, retries=2)


async def relate(items: list[dict], relation_types: list[str]) -> list[Relation]:
    """items: [{"fact", "entities": [names]}] → the relations each statement states between its entities.
    Never raises (none on failure)."""
    if not items:
        return []
    blocks = [f"[{i}] {it['fact']}\n    ENTITIES: {', '.join(it['entities'])}" for i, it in enumerate(items)]
    try:
        out = await _relate.run(f"RELATION TYPES: {', '.join(relation_types)}\n\nSTATEMENTS:\n" + "\n".join(blocks))
        return out.output.relations
    except Exception as e:
        print(f"[Relate] failed: {type(e).__name__}: {e}", flush=True)
        return []
