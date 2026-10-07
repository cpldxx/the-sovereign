"""Strategist — turns what the graph knows into playbooks: "if this happens, do that".

Playbooks are the slow path's pre-computed decisions: written at night from accumulated knowledge, so the
fast path only has to notice that a situation occurred and propose the prepared response.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("strategist")
MAX_PLAYBOOKS = 8


class SensorTrigger(BaseModel):
    sensor: str = Field(description="Name of a SENSOR")
    params: dict = Field(default_factory=dict, description="Its parameters")
    field: str = Field(description="Path to a number in its output, e.g. change_percent or quotes.0.price")
    op: str = Field(description="One of < > <= >=")
    value: float = Field(description="The threshold")
    every_minutes: int = Field(default=60, description="How often to check (15-1440)")

    @field_validator("params", mode="before")
    @classmethod
    def _null_dict(cls, v):
        return {} if v is None else v


class PlaybookDraft(BaseModel):
    uid: str | None = Field(default=None, description="uid of the existing playbook this keeps or revises; null if new")
    name: str = Field(description="Short title")
    situation: str = Field(description="The concrete, observable event that triggers it — something a new source "
                                       "could report or a fact could change to")
    watch: list[str] = Field(description="Names of the graph's entities where that event would show up")
    response: str = Field(description="What to do when it happens and why, grounded in the facts")
    action: str = Field(description="One action name from the ACTIONS catalog")
    evidence: list[str] = Field(default_factory=list, description="uids of the facts that justify this playbook")
    trigger: SensorTrigger | None = Field(default=None, description="Optional live condition on a SENSOR that "
                                                                    "fires the playbook as soon as it is met")

    @field_validator("watch", "evidence", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v


class Playbooks(BaseModel):
    playbooks: list[PlaybookDraft] = Field(default_factory=list, description="New or revised playbooks")
    retire: list[str] = Field(default_factory=list, description="uids of existing playbooks that no longer apply")

    @field_validator("playbooks", "retire", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v

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
- trigger (optional): when the situation shows up in a number a SENSOR reads live (a price move, a count), add a
  condition on it: sensor, params (for the entity it is about), field (one of the sensor's numeric fields), op,
  value, every_minutes — e.g. {{"sensor": "yahoo_stock_price", "params": {{"symbol": "AMD"}}, "field":
  "change_percent", "op": "<=", "value": -8, "every_minutes": 60}}. It is checked around the clock and fires the
  playbook the moment it holds. Only sensors and fields listed below.

Return only the playbooks you ADD or REVISE (a revision keeps the existing uid), and in `retire` the uids of
existing playbooks that no longer apply. Existing playbooks you don't mention stay as they are. At most
{MAX_PLAYBOOKS} active playbooks in all — the most consequential ones. Use only the facts given — no outside
knowledge."""

_agent = Agent(MODEL, name="strategist", model_settings=model_settings(), system_prompt=SYSTEM,
               output_type=Playbooks, retries=3)


async def write_playbooks(context: str) -> Playbooks:
    """Raises on failure (the caller keeps the current playbooks)."""
    return (await _agent.run(context)).output


class TriggerAnswer(BaseModel):
    trigger: SensorTrigger | None = None
    reason: str = ""


TRIGGER_SYSTEM = """You are the Sovereign's Strategist, wiring one PLAYBOOK to live data. Decide whether one of the
SENSORS measures the playbook's situation itself, and if so write the condition that means "it is happening":
sensor, params (for the entity concerned — e.g. its ticker), field (one of the listed numeric fields), op (< > <= >=),
value (on the same scale as the field's current value), every_minutes (15-1440).
Return trigger = null when no sensor measures the situation itself: a share price says nothing about a factory's
capacity or a qualification decision. A wrong trigger is worse than none. Give a one-line reason either way."""

_trigger_agent = Agent(MODEL, name="trigger_writer", model_settings=model_settings(decisive=True),
                       system_prompt=TRIGGER_SYSTEM, output_type=TriggerAnswer, retries=3)


async def write_trigger(prompt: str) -> TriggerAnswer:
    """Raises on failure (the playbook stays without a trigger)."""
    return (await _trigger_agent.run(prompt)).output
