"""Validator — checks every extracted fact against the source text it came from, in one LLM call.

It judges faithfulness to the source, not truth in the world: sources report events newer than
the model's training data, and collecting them is the point.
"""

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("validator")


class FactVerdict(BaseModel):
    index: int
    supported: bool = Field(description="The source text states this fact")
    reliability: float = Field(ge=0.0, le=1.0, description="How far to trust it")
    reason: str = ""


class Verdicts(BaseModel):
    verdicts: list[FactVerdict]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"verdicts": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Validator. You check FACTS extracted from a SOURCE TEXT.

For each fact:
- supported = false when the source text does not actually state it: invented or changed numbers, claims the
  text does not make, the wrong entities, a reversed direction, or a hedge ("may", "plans to") dropped.
- reliability (0 to 1) — how far to trust it, from the source's authority and the claim's specificity:
  0.9 official or primary source · 0.75 reputable reporting with specifics · 0.55 analysis, forecast or
  single unnamed source · 0.35 rumor, speculation or marketing claim.
- Do NOT judge facts against your own knowledge: the source may report events newer than your training data.
Return one verdict per fact, with its index."""

_agent = Agent(MODEL, name="validator", model_settings=model_settings(decisive=True),
               system_prompt=SYSTEM, output_type=Verdicts, retries=3)


async def validate(text: str, source: str, facts: list[str]) -> list[FactVerdict]:
    """Verdicts in the same order as `facts`. Never raises: on failure every fact is unsupported."""
    if not facts:
        return []
    listing = "\n".join(f"[{i}] {f}" for i, f in enumerate(facts))
    prompt = f"SOURCE ({source}):\n\"\"\"\n{text}\n\"\"\"\n\nFACTS:\n{listing}"
    try:
        verdicts = {v.index: v for v in (await _agent.run(prompt)).output.verdicts}
    except Exception as e:
        print(f"[Validator] failed: {type(e).__name__}: {e}", flush=True)
        verdicts = {}
    return [
        verdicts.get(i, FactVerdict(index=i, supported=False, reliability=0.0, reason="not validated"))
        for i in range(len(facts))
    ]
