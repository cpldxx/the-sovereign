"""Strategist — turns what the graph knows into playbooks: "if this happens, do that".

Playbooks are the slow path's pre-computed decisions: written at night from accumulated knowledge, so the
fast path only has to notice that a situation occurred and propose the prepared response.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("strategist")
MAX_PLAYBOOKS = 8


class PlaybookDraft(BaseModel):
    uid: str | None = Field(default=None, description="uid of the existing playbook this keeps or revises; null if new")
    name: str = Field(description="Short title")
    situation: str = Field(description="The concrete, observable event that triggers it — something a new source "
                                       "could report or a fact could change to")
    watch: list[str] = Field(description="Names of the graph's entities where that event would show up")
    response: str = Field(description="What to do when it happens and why, grounded in the facts")
    action: str = Field(description="One action name from the ACTIONS catalog")
    evidence: list[str] = Field(default_factory=list, description="uids of the facts that justify this playbook")

    @field_validator("watch", "evidence", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v


class Playbooks(BaseModel):
    playbooks: list[PlaybookDraft]

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, list):
            return {"playbooks": v}
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = f"""You are the Sovereign's Strategist for one knowledge domain. From what its knowledge graph knows, you
write PLAYBOOKS: pre-decided responses to situations that matter to the owner of this domain.

A good playbook:
- situation: a specific, observable development that new sources could report — a change in a quantity, a
  status, a plan, a relationship ("TSMC's CoWoS lead times rise beyond 78 weeks", "Samsung passes Nvidia's HBM4
  qualification"). Not vague ("the market changes"), not something already true.
- watch: the entities (exact names from the graph) where it would show up.
- response: what the owner should do or know when it happens, and why — reasoning from the CURRENT FACTS
  (cite them). action: the best-fitting action from the ACTIONS catalog (usually alert; draft for prepared
  documents; an external action only when the situation clearly calls for it).
- evidence: uids of the facts behind it.

Return at most {MAX_PLAYBOOKS} playbooks, the most consequential ones. Keep an EXISTING playbook (same uid) if
it still makes sense, revise it (same uid) if the knowledge changed, drop it if it no longer applies. Use only
the facts given — no outside knowledge."""

_agent = Agent(MODEL, name="strategist", model_settings=model_settings(), system_prompt=SYSTEM,
               output_type=Playbooks, retries=3)


async def write_playbooks(context: str) -> list[PlaybookDraft]:
    """Raises on failure (the caller keeps the current playbooks)."""
    return (await _agent.run(context)).output.playbooks[:MAX_PLAYBOOKS]
