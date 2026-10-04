"""LLM selection for the KG agents — everything comes from env, nothing is hard-coded.

    LLM_MODEL=ollama:qwen3.6:35b            default for every agent
    LLM_MODEL_INGESTOR=anthropic:claude-…   optional per-agent override (INGESTOR, GATEKEEPER,
                                            ARCHITECT, ONTOLOGIST)
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


def model_for(agent: str) -> str:
    """The model an agent should use: its own override, else LLM_MODEL."""
    return os.getenv(f"LLM_MODEL_{agent.upper()}") or os.getenv("LLM_MODEL") or DEFAULT_MODEL


def model_settings() -> ModelSettings:
    """Reasoning off by default: the KG agents extract and validate, they don't need to think.

    On qwen3.6:35b (M1 Max) an entity+relation extraction takes ~19 s without thinking and
    ~171 s with it, at no quality gain. pydantic-ai's unified `thinking=False` alone still
    leaves Qwen3 reasoning through Ollama (577 → 281 output tokens); the OpenAI-compatible
    `reasoning_effort="none"` turns it fully off (→ 137). Providers that don't use it ignore it.
    """
    if THINKING:
        return {}
    return {"thinking": False, "openai_reasoning_effort": "none"}
