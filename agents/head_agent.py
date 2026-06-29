"""Head Agent — the Sovereign's CEO, built on Hermes AIAgent.

The Head Agent is KG-aware: it queries accumulated knowledge before answering,
and can ingest new knowledge. It runs on a local Ollama model with a >=64K context
window (Hermes minimum; qwen2.5:32b's 32K is rejected).

Role split:
  Hermes  -> orchestration loop, tool dispatch, provider abstraction
  Sovereign (us) -> KG read/write tools, ontology, ingest pipeline
"""

import os

from dotenv import load_dotenv
from run_agent import AIAgent

from agents.kg_tools import KG_TOOLSET, register_kg_tools
from domains.registry import load_domain

load_dotenv()

# Head Agent model — MUST have >=64K context (Hermes hard minimum;
# qwen2.5:32b's 32K is rejected). Honors OLLAMA_MODEL_HEAD from .env
# (e.g. sovereign-35b / qwen3.6:35b, both 262K). Hermes takes a bare model
# name (base_url is set separately), so strip any "ollama:" provider prefix.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
HEAD_MODEL = os.getenv("OLLAMA_MODEL_HEAD", "qwen3.6:35b").removeprefix("ollama:")


def _system_prompt(domain: str, description: str, ontology: dict) -> str:
    entity_types = ", ".join(ontology.get("entity_types", []))
    relation_types = ", ".join(ontology.get("relation_types", []))
    return f"""You are the Head Agent of The Sovereign — an autonomous domain expert for the "{domain}" domain.
Domain: {description}

You are not a generic assistant. You are the CEO of a knowledge operation:
- You have a growing knowledge graph (KG) of validated, structured facts about this domain.
- The KG ontology — entity types [{entity_types}] and relations [{relation_types}] — is the grammar of everything you know.

How you work:
1. BEFORE answering any substantive question, call query_knowledge_graph to ground yourself in what the system actually knows. Never answer domain questions from imagination when the KG can inform you.
2. When you encounter new factual information worth keeping, call ingest_data to persist it (it is validated and ontology-constrained before storage).
3. Cite KG evidence (node uids, reliability) when you reason. Reliability matters — flag low-confidence knowledge.
4. Be decisive and concrete. You act on accumulated knowledge, not on a single snapshot.

Answer the user grounded in the knowledge graph."""


def create_head_agent(domain: str, *, max_iterations: int = 8, quiet: bool = True) -> AIAgent:
    """Build a domain-scoped Head Agent with KG tools registered.

    Registering KG tools mutates Hermes's global registry (override=True), bound to
    this domain. Runs are sequential per task, so this is safe for now.
    """
    dom = load_domain(domain)
    config = dom["config"]
    ontology = dom.get("ontology", {})

    register_kg_tools(domain)

    return AIAgent(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",
        provider="openai",
        api_mode="chat_completions",
        model=HEAD_MODEL,
        enabled_toolsets=[KG_TOOLSET],
        ephemeral_system_prompt=_system_prompt(
            domain, config.get("description", domain), ontology
        ),
        max_iterations=max_iterations,
        tool_delay=0.0,
        quiet_mode=quiet,
    )


def ask_head(domain: str, message: str, *, max_iterations: int = 8) -> str:
    """One-shot: build the Head Agent for `domain` and get a grounded answer."""
    agent = create_head_agent(domain, max_iterations=max_iterations)
    return agent.chat(message)
