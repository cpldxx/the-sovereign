"""Runs one DeerFlow research turn and collects what it read.

DeerFlow's embedded client is synchronous; the service calls `run()` in a worker thread. Runs are
sequential: research and the KG pipeline share one local model, so parallel runs would only slow both.
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

_client: DeerFlowClient | None = None


def search_provider() -> str:
    """Which web search the agent uses: an API when a key is set, else the local SearXNG."""
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    return "searxng"


def _resolved_config() -> str:
    """config.yaml with the web_search tool chosen from env, written next to DeerFlow's state."""
    config = yaml.safe_load(CONFIG.read_text())
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
    return str(out)


def _get_client() -> DeerFlowClient:
    global _client
    if _client is None:
        _client = DeerFlowClient(config_path=_resolved_config(), thinking_enabled=False, subagent_enabled=False)
    return _client


def run(prompt: str, on_step: Callable[[str], None], thread_id: str | None = None) -> tuple[str, list[fetch.Page]]:
    """One research turn. Returns (the agent's report, every page it tried to read).
    `thread_id` (the job id) groups the run's LangFuse traces into one session.

    A run that stops early (step limit, model error) still returns the pages it read — those are
    what the graph needs; the report is only a summary. It raises only when nothing was read."""
    capture = fetch.current = fetch.Capture()
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
    return os.getenv("RESEARCH_OLLAMA_MODEL", "")


def tracing() -> bool:
    """LangFuse tracing through DeerFlow's built-in callback (LANGFUSE_TRACING + keys in .env)."""
    from deerflow.config import get_enabled_tracing_providers

    return "langfuse" in get_enabled_tracing_providers()
