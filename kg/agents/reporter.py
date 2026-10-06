"""Reporter — writes the daily briefing from what changed in a domain's graph.

The digest (new sources, entities, facts, strengthened and superseded facts, review decisions, research
runs) is assembled in code from the graph itself; the Reporter only turns it into a briefing to read and
a short version to speak. REPORT_LANGUAGE sets the language (default English).
"""

import os

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("reporter")
LANGUAGE = os.getenv("REPORT_LANGUAGE", "English")


class Briefing(BaseModel):
    headline: str = Field(description="One sentence: the most important change")
    briefing: str = Field(description="Markdown briefing")
    spoken: str = Field(description="3-5 plain sentences to be read aloud")

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Reporter. You write the daily briefing for the owner of a knowledge domain from
a DIGEST of what changed in its knowledge graph over the period. Write in {language}.

- headline: the single most important change, one sentence.
- briefing (markdown), three short sections with bullets:
  "## What changed" — the most significant new and updated knowledge, grouped by theme; cite numbers and dates.
  "## Signals to watch" — superseded facts (what changed from what), facts confirmed by more sources, weak
  signals (weight < 0.5) worth following. Skip the section if there are none.
  "## Needs attention" — failed research runs, unreadable or partial sources, pending review items, and
  ontology gaps that recur (a missing type or relation many items needed: suggest adding it). Skip the section if
  there is nothing.
- spoken: 3-5 natural sentences for text-to-speech — no markdown, no URLs, no uids; numbers as digits.
- Use ONLY the digest. Every number you write must appear in the digest. Never invent facts, numbers or causes.
  No citation markers or source labels — the report links its sources itself.
  Prefer the few things that matter over listing everything; say so plainly when little changed."""

_agent = Agent(MODEL, name="reporter", model_settings=model_settings(),
               system_prompt=SYSTEM.format(language=LANGUAGE), output_type=Briefing, retries=3)


async def write_briefing(digest_text: str) -> Briefing:
    """Raises on failure — the caller falls back to a plain listing."""
    return (await _agent.run(digest_text)).output
