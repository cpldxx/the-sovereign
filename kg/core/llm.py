"""LLM selection for the KG agents — everything comes from env, nothing is hard-coded.

    LLM_MODEL=ollama:qwen3.6:35b            default for every agent
    LLM_MODEL_EXTRACTOR=anthropic:claude-…  optional per-agent override (EXTRACTOR, VALIDATOR,
                                            RESOLVER, LINKER, ONTOLOGIST, SUMMARIZER, REPORTER)
    LLM_THINKING=false                      reasoning off unless set to true
    ANTHROPIC_API_KEY=…                     only needed for anthropic: models

Model strings are pydantic-ai's "<provider>:<model>" (ollama:, anthropic:, openai:, …).
"""

import os

from dotenv import load_dotenv
from pydantic_ai.settings import ModelSettings

load_dotenv()

DEFAULT_MODEL = "ollama:qwen3.6:35b"
THINKING = os.getenv("LLM_THINKING", "false").strip().lower() in ("1", "true", "yes", "on")
# Sampling temperature for the agents that make yes/no decisions (Validator, Resolver, Linker). Replaying the
# Linker on recorded inputs, two runs agreed on 7/23 decisions at the model's default and 10/20 at 0.1.
DECISION_TEMPERATURE = float(os.getenv("LLM_DECISION_TEMPERATURE", "0.1"))


def model_for(agent: str) -> str:
    """The model an agent should use: its own override, else LLM_MODEL."""
    return os.getenv(f"LLM_MODEL_{agent.upper()}") or os.getenv("LLM_MODEL") or DEFAULT_MODEL


def model_settings(decisive: bool = False) -> ModelSettings:
    """Reasoning off by default: the KG agents extract and validate, they don't need to think.

    On qwen3.6:35b (M1 Max) an entity+relation extraction takes ~19 s without thinking and
    ~171 s with it, at no quality gain. pydantic-ai's unified `thinking=False` alone still
    leaves Qwen3 reasoning through Ollama (577 → 281 output tokens); the OpenAI-compatible
    `reasoning_effort="none"` turns it fully off (→ 137). Providers that don't use it ignore it.
    `decisive`: a low temperature for agents that judge rather than write.
    """
    settings: ModelSettings = {} if THINKING else {"thinking": False, "openai_reasoning_effort": "none"}
    if decisive:
        settings["temperature"] = DECISION_TEMPERATURE
    return settings

