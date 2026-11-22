"""Runs one DeerFlow research turn and collects what it read.

DeerFlow's embedded client is synchronous; the service calls `run()` in a worker thread. Runs are
sequential: research and the KG pipeline share one local model, so parallel runs would only slow both.

The model is RESEARCH_MODEL, "<provider>:<model>" like the KG's and the Head's: ollama:qwen3.6:35b (the default) or
anthropic:claude-opus-5-5 (ANTHROPIC_API_KEY). RESEARCH_OLLAMA_MODEL=<model> is the older spelling of ollama:<model>.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path

import yaml
from deerflow.client import DeerFlowClient

from sovereign_research import fetch

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
# Graph steps per run (each model turn and tool call counts). The embedded client ignores config.yaml's
# recursion_limit and defaults to 100 — too few for reading many pages — so it is passed per run.
RECURSION_LIMIT = int(os.getenv("RESEARCH_RECURSION_LIMIT", "400"))
DEFAULT_MODEL = "qwen3.6:35b"

_client: DeerFlowClient | None = None


def search_provider() -> str:
    """Which web search the agent uses: an API when a key is set, else the local SearXNG."""
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    return "searxng"


def research_model() -> tuple[str, str]:
    """(provider, model) of the research agent."""
    spec = os.getenv("RESEARCH_MODEL") or f"ollama:{os.getenv('RESEARCH_OLLAMA_MODEL') or DEFAULT_MODEL}"
    provider, _, model = spec.partition(":")
    if provider not in ("ollama", "anthropic") or not model:
        raise ValueError(f"RESEARCH_MODEL must be ollama:<model> or anthropic:<model>, got {spec!r}")
    return provider, model


def _model_entry(entry: dict) -> dict:
    """config.yaml's model, for the provider RESEARCH_MODEL names."""
    provider, model = research_model()
    if provider == "ollama":
        return {**entry, "model": model}
    # Claude through its API key (ChatAnthropic reads ANTHROPIC_API_KEY itself, so no key lands in the written file).
    # Not DeerFlow's own claude_provider: that one signs in as Claude Code. No temperature, no thinking switch: the
    # current Claude models reject sampling settings and disabled thinking with a 400. max_tokens as for any request
    # that isn't streamed (the SDK refuses larger ones without streaming).
    return {"name": entry["name"], "display_name": "Research model (Claude)",
            "use": "langchain_anthropic:ChatAnthropic", "model": model, "max_tokens": 16000,
            "context_window": entry.get("context_window", 65536),
            "supports_thinking": False, "supports_vision": False}


def _resolved_config() -> str:
    """config.yaml with the model and the web_search tool chosen from env, written next to DeerFlow's state."""
    config = yaml.safe_load(CONFIG.read_text())
    config["models"] = [_model_entry(config["models"][0])]
    provider = search_provider()
    if provider != "searxng":
        key = os.environ["TAVILY_API_KEY" if provider == "tavily" else "BRAVE_API_KEY"]
        for t in config["tools"]:
            if t["name"] == "web_search":
                t.clear()
                t.update(name="web_search", group="web", max_results=8, api_key=key,
                         use=f"deerflow.community.{provider}.tools:web_search_tool")
    out = Path(os.environ["DEER_FLOW_HOME"]) / "config.resolved.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(config, sort_keys=False))
    out.chmod(0o600)  # may hold an API key
    # Parts of DeerFlow load the config on their own (get_app_config) and look for config.yaml in the working
    # directory — research/ in development, /data in Docker, where nothing is found and every run failed
    # (2026-10-11). The variable points them at this same file.
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(out)
    return str(out)


def config_ok() -> bool:
    """DeerFlow can load its configuration the way its own modules do (get_app_config, not just our client)."""
    try:
        if "DEER_FLOW_CONFIG_PATH" not in os.environ:
            _resolved_config()
        from deerflow.config.app_config import get_app_config  # noqa: PLC0415
        get_app_config()
        return True
    except Exception:
        return False


def _get_client() -> DeerFlowClient:
    global _client
    if _client is None:
        _client = DeerFlowClient(config_path=_resolved_config(), thinking_enabled=False, subagent_enabled=False)
    return _client


def run(prompt: str, on_step: Callable[[str], None], thread_id: str | None = None,
        domain: str = "") -> tuple[str, list[fetch.Page]]:
    """One research turn. Returns (the agent's report, every page it tried to read).
    `thread_id` (the job id) groups the run's LangFuse traces into one session; the pages `domain`'s graph has are
    kept from the agent (fetch.known).

    A run that stops early (step limit, model error) still returns the pages it read — those are
    what the graph needs; the report is only a summary. It raises only when nothing was read."""
    capture = fetch.current = fetch.Capture(domain=domain)
    texts: dict[str, str] = {}   # ai message id → accumulated text (deltas)
    order: list[str] = []
    start = time.monotonic()
    try:
        for ev in _get_client().stream(prompt, thread_id=thread_id, recursion_limit=RECURSION_LIMIT):
            if ev.type != "messages-tuple" or not isinstance(ev.data, dict):
                continue
            data = ev.data
            if data.get("type") == "ai":
                for call in data.get("tool_calls") or []:
                    args = call.get("args") or {}
                    target = args.get("query") or args.get("url") or ""
                    on_step(f"[{time.monotonic() - start:4.0f}s] {call.get('name')}: {str(target)[:160]}")
                if data.get("content"):
                    mid = data.get("id") or str(len(order))
                    if mid not in texts:
                        order.append(mid)
                        texts[mid] = ""
                    texts[mid] += data["content"] if isinstance(data["content"], str) else str(data["content"])
    except Exception as e:
        if not any(p.status != "failed" for p in capture.pages):
            raise
        on_step(f"research stopped early ({type(e).__name__}); keeping the {len(capture.pages)} page(s) read so far")
        return "", list(capture.pages)
    finally:
        fetch.current = None
    return (texts[order[-1]].strip() if order else ""), list(capture.pages)


def model_name() -> str:
    """For /health: a RESEARCH_MODEL that can't be read is shown as it is (config_ok() reports it), not a 500."""
    try:
        return ":".join(research_model())
    except ValueError:
        return f"{os.getenv('RESEARCH_MODEL', '')} (not ollama:<model> or anthropic:<model>)"


def tracing() -> bool:
    """LangFuse tracing through DeerFlow's built-in callback (LANGFUSE_TRACING + keys in .env)."""
    from deerflow.config import get_enabled_tracing_providers

    return "langfuse" in get_enabled_tracing_providers()
