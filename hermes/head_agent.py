"""Head Agent — the Sovereign's CEO, built on Hermes AIAgent.

The Head Agent never touches the database. Its only KG access is the Sovereign
KG's MCP server, registered into Hermes as the `mcp-sovereign` toolset:
query_knowledge_graph, ingest_data, get_ontology, update_ontology, list_domains.
Domain context (description, ontology) comes from the KG's REST API.

Every tool takes the domain explicitly, so agents for different domains can run
side by side without rebinding anything global.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
# Keep Hermes state (sessions, logs, memory) inside this project, not ~/.hermes.
# Must be set before any Hermes module is imported.
os.environ.setdefault("HERMES_HOME", str(HERE / ".hermes-home"))

from run_agent import AIAgent  # noqa: E402
from tools.mcp_tool import register_mcp_servers  # noqa: E402

KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# "<provider>:<model>" like the KG agents: ollama:qwen3.6:35b or anthropic:claude-sonnet-5-5.
# Hermes requires a >=64K context window (qwen2.5:32b's 32K is rejected).
HEAD_MODEL = os.getenv("HEAD_MODEL", "ollama:qwen3.6:35b")
HEAD_THINKING = os.getenv("HEAD_THINKING", "false").strip().lower() in ("1", "true", "yes", "on")

MCP_SERVER = "sovereign"
KG_TOOLSET = f"mcp-{MCP_SERVER}"
_TOOL_PREFIX = f"mcp__{MCP_SERVER}__"

# Progress events for live UIs: {"type": "tool_start" | "tool_end" | "delta", ...}
EventSink = Callable[[dict], None]


class DomainNotFound(LookupError):
    pass


def connect_kg() -> list[str]:
    """Connect to the KG's MCP server and register its tools. Idempotent; retries a failed connect."""
    return register_mcp_servers({
        MCP_SERVER: {
            "url": f"{KG_URL}/mcp",
            "timeout": 900,  # ingest_data runs the full LLM pipeline (minutes)
            "connect_timeout": 30,
            # The KG exposes tools only; skip Hermes's resource/prompt helper tools.
            "tools": {"resources": False, "prompts": False},
        }
    })


def _domain_context(domain: str) -> tuple[str, dict]:
    """Domain description + ontology from the KG REST API."""
    with httpx.Client(base_url=KG_URL, timeout=10.0) as client:
        r = client.get(f"/domains/{domain}")
        if r.status_code == 404:
            raise DomainNotFound(domain)
        r.raise_for_status()
        description = r.json()["config"].get("description") or domain
        ontology = client.get(f"/domains/{domain}/ontology").json()["ontology"]
    return description, ontology


def _system_prompt(domain: str, description: str, ontology: dict) -> str:
    entity_types = ", ".join(ontology.get("entity_types", []))
    relation_types = ", ".join(ontology.get("relation_types", []))
    return f"""You are the Head Agent of The Sovereign — an autonomous domain expert for the "{domain}" domain.
Domain: {description}

You are not a generic assistant. You are the CEO of a knowledge operation:
- You have a growing knowledge graph (KG) of validated, structured facts about this domain.
- The KG ontology — entity types [{entity_types}] and relations [{relation_types}] — is the grammar of everything you know.
- Every KG tool takes a `domain` argument. Your domain is always "{domain}".

How you work:
1. BEFORE answering any substantive question, call query_knowledge_graph to ground yourself in what the system actually knows. Never answer domain questions from imagination when the KG can inform you. If the KG has nothing relevant, say so plainly.
2. When the user gives you new factual information worth keeping (or says "ingest this"), call ingest_data to persist it. It is validated and ontology-constrained before storage; report what was stored and rejected.
3. Cite KG evidence (node uids, reliability) when you reason. Reliability matters — flag low-confidence knowledge.
4. You alone may change the ontology (update_ontology), and only when accumulated evidence shows the current grammar cannot express it. Never for a single fact.
5. Be decisive and concrete. You act on accumulated knowledge, not on a single snapshot."""


def _unwrap(raw):
    """Tool result → parsed JSON. Hermes wraps MCP text content as {"result": "<json text>"}."""
    data = raw
    for _ in range(3):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return data
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        else:
            return data
    return data


def _touched_uids(tool: str, data) -> list[str]:
    """KG node uids a tool call read or wrote — lets a UI highlight them in the graph."""
    if not isinstance(data, dict):
        return []
    if tool == "query_knowledge_graph":
        return [n["uid"] for n in data.get("matches", []) + data.get("neighbors", []) if "uid" in n]
    if tool == "ingest_data":
        return [d["uid"] for d in data.get("details", []) if d.get("status") == "stored"]
    return []


def _progress_callbacks(on_event: EventSink) -> dict:
    """Hermes callbacks → plain JSON-able events (tool names without the MCP prefix)."""

    def name(tool: str) -> str:
        return tool.removeprefix(_TOOL_PREFIX)

    def args(raw) -> dict | str:
        return raw if isinstance(raw, dict) else str(raw)

    def tool_end(call_id, tool, _args, raw) -> None:
        tool = name(tool)
        data = _unwrap(raw)
        text = data if isinstance(data, str) else json.dumps(data, default=str)
        on_event({
            "type": "tool_end",
            "id": call_id,
            "name": tool,
            "uids": _touched_uids(tool, data),
            "result": text if len(text) <= 2000 else text[:2000] + "…",
        })

    return {
        "tool_start_callback": lambda call_id, tool, a: on_event(
            {"type": "tool_start", "id": call_id, "name": name(tool), "args": args(a)}
        ),
        "tool_complete_callback": tool_end,
        # None marks the end of a streamed message; only text is forwarded.
        "stream_delta_callback": lambda text: text and on_event({"type": "delta", "text": text}),
    }


def _model_kwargs() -> dict:
    """AIAgent connection settings for HEAD_MODEL (Ollama or Anthropic)."""
    provider, _, model = HEAD_MODEL.partition(":")
    if provider == "anthropic":
        return {"provider": "anthropic", "model": model, "api_key": os.getenv("ANTHROPIC_API_KEY")}
    if provider != "ollama":
        raise ValueError(f"HEAD_MODEL must start with ollama: or anthropic:, got {HEAD_MODEL!r}")
    kwargs = {
        "provider": "openai",
        "api_mode": "chat_completions",
        "base_url": OLLAMA_BASE_URL,
        "api_key": "ollama",
        "model": model,
    }
    if not HEAD_THINKING:
        # Ollama's OpenAI-compatible API turns Qwen3 reasoning fully off with this
        # (Hermes merges request_overrides into every chat request).
        kwargs["request_overrides"] = {"reasoning_effort": "none"}
    return kwargs


def create_head_agent(domain: str, *, max_iterations: int = 8, on_event: EventSink | None = None) -> AIAgent:
    """Build a domain-scoped Head Agent whose only tools are the KG's MCP tools."""
    description, ontology = _domain_context(domain)
    connect_kg()
    return AIAgent(
        **_model_kwargs(),
        enabled_toolsets=[KG_TOOLSET],
        ephemeral_system_prompt=_system_prompt(domain, description, ontology),
        max_iterations=max_iterations,
        tool_delay=0.0,
        quiet_mode=True,
        skip_context_files=True,
        **(_progress_callbacks(on_event) if on_event else {}),
    )


def ask_head(
    domain: str,
    message: str,
    history: list[dict] | None = None,
    *,
    max_iterations: int = 8,
    on_event: EventSink | None = None,
) -> str:
    """One turn with the Head Agent. `history` is prior [{role, content}] turns, oldest first.

    `on_event` (optional) receives live progress events while the agent works.
    """
    agent = create_head_agent(domain, max_iterations=max_iterations, on_event=on_event)
    result = agent.run_conversation(message, conversation_history=history or None)
    return result["final_response"]
