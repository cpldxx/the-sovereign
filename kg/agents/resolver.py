"""Resolver — decides whether new entities are the same real-world thing as existing ones.

Only called for the ambiguous cases: exact name-key matches are merged in code, and entities with
no embedding candidates are simply new. One LLM call covers every ambiguous entity of an ingest.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("resolver")


class Resolution(BaseModel):
    index: int
    duplicate_of: str | None = Field(default=None, description="uid of the existing entity that is the same thing")
    confident: bool = True

    @field_validator("confident", mode="before")
    @classmethod
    def _null_flag(cls, v):
        return False if v is None else v  # qwen3.6 sometimes writes null; rejecting it regenerated the answer


class Resolutions(BaseModel):
    resolutions: list[Resolution]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"resolutions": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Resolver. For each NEW entity, decide whether it is the SAME real-world thing
as one of its CANDIDATES (existing entities).

- Same thing under different names → duplicate_of = that candidate's uid.
  ("TSMC" = "Taiwan Semiconductor Manufacturing Company"; "the Fed" = "Federal Reserve")
- Related but different → duplicate_of = null: variants, versions, generations, parts, products of a company,
  subsidiaries, competitors. ("CoWoS" ≠ "CoWoS-L"; "HBM3" ≠ "HBM3E"; "Nvidia" ≠ "Nvidia Blackwell")
- If you are not sure, give your best answer and set confident = false.
Return one resolution per new entity, with its index."""

_agent = Agent(MODEL, name="resolver", model_settings=model_settings(decisive=True),
               system_prompt=SYSTEM, output_type=Resolutions, retries=3)


async def resolve(items: list[dict]) -> list[Resolution]:
    """items: [{"name", "type", "description", "candidates": [{"uid", "name", "type", "summary"}]}].
    Never raises: on failure nothing is merged (every entity stays new, unconfident)."""
    if not items:
        return []
    blocks = []
    for i, it in enumerate(items):
        cands = "\n".join(f"    - uid={c['uid']} | {c['name']} ({c['type']}): {c['summary']}" for c in it["candidates"])
        blocks.append(f"[{i}] NEW: {it['name']} ({it['type']}): {it['description']}\n  CANDIDATES:\n{cands}")
    try:
        found = {r.index: r for r in (await _agent.run("\n\n".join(blocks))).output.resolutions}
    except Exception as e:
        print(f"[Resolver] failed: {type(e).__name__}: {e}", flush=True)
        found = {}
    valid = [{c["uid"] for c in it["candidates"]} for it in items]
    out = []
    for i in range(len(items)):
        r = found.get(i, Resolution(index=i, duplicate_of=None, confident=False))
        if r.duplicate_of and r.duplicate_of not in valid[i]:  # hallucinated uid
            r = Resolution(index=i, duplicate_of=None, confident=False)
        out.append(r)
    return out
