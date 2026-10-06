"""Watcher — decides whether new knowledge triggers a playbook, and fills in its action.

Only called for playbooks whose watched entities gained, confirmed or lost facts in the period.
"""

import json

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("watcher")


class Verdict(BaseModel):
    triggered: bool = Field(description="The NEW facts show the playbook's situation has happened")
    rationale: str = Field(default="", description="Why, citing the new facts and their weights")
    evidence: list[str] = Field(default_factory=list, description="uids of the facts that show it")
    params: dict = Field(default_factory=dict, description="Parameters for the playbook's action")

    @field_validator("evidence", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v

    @field_validator("params", mode="before")
    @classmethod
    def _null_dict(cls, v):
        return {} if v is None else v

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Watcher. You get a PLAYBOOK (a situation to watch for and the response prepared
for it) and the NEW FACTS the knowledge graph learned about the entities it watches.

- triggered = true only if the new facts show that the situation has actually happened (or clearly is happening).
  Related news, an unchanged state or a weak hint (weight < 0.5) is not enough. When in doubt: false.
- If triggered: rationale = what happened, citing the new facts with their weights; evidence = their uids;
  params = the playbook action's parameters, filled in for what happened (follow the PARAMETERS spec).
- Use only the facts given."""

_agent = Agent(MODEL, name="watcher", model_settings=model_settings(decisive=True), system_prompt=SYSTEM,
               output_type=Verdict, retries=3)


async def judge(playbook: dict, action: dict, facts: list[dict]) -> Verdict:
    """Never raises: on failure the playbook is not triggered."""
    params = "\n".join(f"  - {p['name']} ({p['type']}{', required' if p.get('required', True) else ''}): "
                       f"{p['description']}" for p in action["params"])
    lines = "\n".join(
        f"- uid={f['uid']} ({f['weight']:.2f}, {f['change']}) {f['fact']}"
        + (f" [no longer true: {f['invalid_reason']}]" if f.get("invalid_reason") else "")
        for f in facts
    )
    prompt = (f"PLAYBOOK: {playbook['name']}\nSITUATION: {playbook['situation']}\nRESPONSE: {playbook['response']}\n"
              f"ACTION: {action['name']} — {action['description']}\nPARAMETERS:\n{params}\n\nNEW FACTS:\n{lines}")
    try:
        return (await _agent.run(prompt)).output
    except Exception as e:
        print(f"[Watcher] failed: {type(e).__name__}: {e}", flush=True)
        return Verdict(triggered=False, rationale=f"watcher failed: {json.dumps(str(e))[:200]}")
