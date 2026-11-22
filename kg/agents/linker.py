"""Linker — compares new facts with the existing facts they could restate or contradict.

A restatement strengthens the existing synapse (more evidence); a newer value invalidates the old
fact instead of deleting it. One LLM call covers every new fact that has candidates; facts without
candidates never reach it.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("linker")


class Link(BaseModel):
    index: int
    same_as: str | None = Field(default=None, description="uid of an existing fact that says the same thing")
    supersedes: list[str] = Field(default_factory=list, description="uids of existing facts no longer true")
    confident: bool = True

    # qwen3.6 often writes null for an empty list or an unknown flag; rejecting that regenerated the whole answer.
    @field_validator("supersedes", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v

    @field_validator("confident", mode="before")
    @classmethod
    def _null_flag(cls, v):
        return False if v is None else v


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
  quantity, a changed status, a reversal, a cancelled plan. Only when the two cannot both be true: the same
  measure of the same thing (same period, same scope) with another value, or the same status changed. The new
  fact must be the later one: when both are dated, one about an earlier date never supersedes one about a later date.
- Facts that are merely related, or that can both be true at once → neither. Another measure (one quarter vs
  six months, a share vs a count), another part, another property of the same thing is not an update:
  "The museum opened in 1902" and "The museum holds 40,000 works" are both true. Most new facts supersede
  nothing.
- If you are not sure about a same_as or supersedes decision, still give it and set confident = false.
Return one link per new fact, with its index."""

_agent = Agent(MODEL, name="linker", model_settings=model_settings("linker", decisive=True),
               system_prompt=SYSTEM, output_type=Links, retries=3)


async def link(items: list[dict]) -> list[Link]:
    """items: [{"fact": str, "candidates": [{"uid", "fact"}]}]. Never raises: on failure nothing links."""
    return await link_or_none(items) or [Link(index=i) for i in range(len(items))]


async def link_or_none(items: list[dict]) -> list[Link] | None:
    """link — but None when the model failed, for a caller that must not take a failure for "no link"."""
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
        return None
    out = []
    for i, it in enumerate(items):
        allowed = {c["uid"] for c in it["candidates"]}
        lk = found.get(i, Link(index=i))
        lk.same_as = lk.same_as if lk.same_as in allowed else None
        lk.supersedes = [u for u in lk.supersedes if u in allowed and u != lk.same_as]
        out.append(lk)
    return out
