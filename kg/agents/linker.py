"""Linker — compares new facts with the existing facts they could restate or contradict.

A restatement strengthens the existing synapse (more evidence); a newer value invalidates the old
fact instead of deleting it. One LLM call covers every new fact that has candidates; facts without
candidates never reach it.
"""

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("linker")


class Link(BaseModel):
    index: int
    same_as: str | None = Field(default=None, description="uid of an existing fact that says the same thing")
    supersedes: list[str] = Field(default_factory=list, description="uids of existing facts no longer true")
    confident: bool = True


class Links(BaseModel):
    links: list[Link]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"links": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Linker. For each NEW fact, compare it with its EXISTING facts.

- same_as: the uid of an existing fact that says the same thing — a restatement, possibly worded differently
  or with a little more or less detail, but no different in substance. At most one.
- supersedes: uids of existing facts that the new fact shows are NO LONGER TRUE — a newer value of the same
  quantity, a changed status, a reversal, a cancelled plan.
- Facts that are merely related, or that can both be true at once → neither.
- If you are not sure about a same_as or supersedes decision, still give it and set confident = false.
Return one link per new fact, with its index."""

_agent = Agent(MODEL, model_settings=model_settings(), system_prompt=SYSTEM, output_type=Links, retries=3)


async def link(items: list[dict]) -> list[Link]:
    """items: [{"fact": str, "candidates": [{"uid", "fact"}]}]. Never raises: on failure nothing links."""
    if not items:
        return []
    blocks = []
    for i, it in enumerate(items):
        cands = "\n".join(f"    - uid={c['uid']}: {c['fact']}" for c in it["candidates"])
        blocks.append(f"[{i}] NEW: {it['fact']}\n  EXISTING:\n{cands}")
    try:
        found = {lk.index: lk for lk in (await _agent.run("\n\n".join(blocks))).output.links}
    except Exception as e:
        print(f"[Linker] failed: {type(e).__name__}: {e}", flush=True)
        found = {}
    out = []
    for i, it in enumerate(items):
        allowed = {c["uid"] for c in it["candidates"]}
        lk = found.get(i, Link(index=i))
        lk.same_as = lk.same_as if lk.same_as in allowed else None
        lk.supersedes = [u for u in lk.supersedes if u in allowed and u != lk.same_as]
        out.append(lk)
    return out
