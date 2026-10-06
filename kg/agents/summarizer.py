"""Summarizer — rewrites entity summaries from what the graph now knows about them.

An entity's summary starts as one sentence from the first source that named it; as facts are added,
strengthened and superseded it goes stale. The nightly digest rewrites the summaries of entities whose
facts changed, from their current valid facts. One LLM call covers a batch of entities.
"""

from pydantic import BaseModel, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("summarizer")
FACTS_PER_ENTITY = 20


class EntitySummary(BaseModel):
    index: int
    summary: str


class Summaries(BaseModel):
    summaries: list[EntitySummary]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"summaries": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Summarizer. For each ENTITY, write its summary: 1-3 sentences saying what it is
and what is currently known about it, from its CURRENT FACTS only.

- Lead with what the entity is; then its most important current facts (keep key numbers and dates).
- Prefer strong facts (high weight); do not present weak ones (weight < 0.5) as certain.
- Use no outside knowledge and do not speculate. No lists, no markdown.
Return one summary per entity, with its index."""

_agent = Agent(MODEL, name="summarizer", model_settings=model_settings(), system_prompt=SYSTEM,
               output_type=Summaries, retries=3)


async def summarize(items: list[dict]) -> dict[int, str]:
    """items: [{"name", "type", "summary", "facts": [{"fact", "weight"}]}] → {index: new summary}.
    Never raises: entities missing from the answer keep their summary."""
    if not items:
        return {}
    blocks = []
    for i, it in enumerate(items):
        facts = "\n".join(f"    - ({f['weight']:.2f}) {f['fact']}" for f in it["facts"][:FACTS_PER_ENTITY])
        blocks.append(f"[{i}] {it['name']} ({it['type']}) — current summary: {it['summary'] or '(none)'}\n"
                      f"  CURRENT FACTS:\n{facts}")
    try:
        out = (await _agent.run("\n\n".join(blocks))).output.summaries
    except Exception as e:
        print(f"[Summarizer] failed: {type(e).__name__}: {e}", flush=True)
        return {}
    return {s.index: s.summary.strip() for s in out if 0 <= s.index < len(items) and s.summary.strip()}
