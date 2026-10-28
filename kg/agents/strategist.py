"""Strategist — decides what the graph should learn next.

From what the graph is unsure or silent about (core/learning.gaps: facts on one weak source, well-connected entities
gone quiet, questions users asked that it could not answer, words the ontology lacked) it writes the research
questions a learning cycle pursues.
"""

from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.llm import model_for, model_settings

MODEL = model_for("strategist")


class ResearchQuestion(BaseModel):
    question: str = Field(description="One research question a web search can answer, specific and self-contained")
    why: str = Field(description="Which gap it closes, in one short sentence")


class ResearchQuestions(BaseModel):
    questions: list[ResearchQuestion] = Field(default_factory=list)


CURIOUS = """You decide what the "{domain}" knowledge graph should learn next. Domain: {description}
Today is {today}. Ask about the current state — this year and the latest — never about years long past unless the
gap is about history (the model's own sense of "now" is older than today).
Below is what the graph is unsure or silent about. Write at most {n} research questions that would close the most
important gaps for this domain. When the domain has a research charter, it says what matters: a core question the
graph cannot answer well yet comes first, and nothing it says to leave out is researched. Then confirm what rests on
one weak source, refresh what may be stale, answer what users asked and the graph could not, and find what the
ontology lacked words for. Each question is specific (names, the period), self-contained and answerable from public
sources. Skip anything off-domain or trivial. Fewer good questions beat many vague ones; none is fine if nothing
matters."""


async def research_questions(domain: str, description: str, gaps: str, n: int) -> ResearchQuestions:
    agent = Agent(MODEL, name="strategist", model_settings=model_settings(), output_type=ResearchQuestions, retries=2,
                  system_prompt=CURIOUS.format(domain=domain, description=description, n=n,
                                               today=date.today().isoformat()))
    out = (await agent.run(gaps)).output
    out.questions = out.questions[:n]
    return out
