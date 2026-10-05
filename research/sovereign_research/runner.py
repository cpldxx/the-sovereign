"""Runs one DeerFlow research turn and collects what it read.

DeerFlow's embedded client is synchronous; the service calls `run()` in a worker thread. Runs are
sequential: research and the KG pipeline share one local model, so parallel runs would only slow both.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path

from deerflow.client import DeerFlowClient

from sovereign_research import fetch

CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

_client: DeerFlowClient | None = None


def _get_client() -> DeerFlowClient:
    global _client
    if _client is None:
        _client = DeerFlowClient(config_path=str(CONFIG), thinking_enabled=False, subagent_enabled=False)
    return _client


def run(prompt: str, on_step: Callable[[str], None]) -> tuple[str, list[fetch.Page]]:
    """One research turn. Returns (the agent's report, every page it tried to read).

    A run that stops early (step limit, model error) still returns the pages it read — those are
    what the graph needs; the report is only a summary. It raises only when nothing was read."""
    capture = fetch.current = fetch.Capture()
    texts: dict[str, str] = {}   # ai message id → accumulated text (deltas)
    order: list[str] = []
    start = time.monotonic()
    try:
        for ev in _get_client().stream(prompt):
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
