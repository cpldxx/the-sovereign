"""Head Agent — the Sovereign's CEO, built on Hermes AIAgent.

The Head Agent never touches the database. Its only KG access is the Sovereign
KG's MCP server, registered into Hermes once per domain (toolset `mcp-kg_<tools>_<domain>`)
and limited to that domain on the KG side — whatever a prompt says, a Head can't reach
another domain's graph. Editors get the full tool set (/mcp), viewers the read-only one
(/mcp/readonly). Domain context (description, ontology) comes from the KG's REST API.
"""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
# Keep Hermes state (sessions, logs, memory) inside this project, not ~/.hermes.
# Must be set before any Hermes module is imported.
os.environ.setdefault("HERMES_HOME", str(HERE / ".hermes-home"))

# LangFuse tracing (optional), through Hermes' bundled langfuse plugin. The Sovereign services share
# LANGFUSE_* keys; the plugin reads HERMES_LANGFUSE_* and is opt-in, so it is enabled here. Plugins are
# discovered when Hermes is imported, so this too must run first.
TRACING = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _enable_hermes_plugin(name: str) -> None:
    import yaml

    path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
    config = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    if name not in enabled:
        enabled.append(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config, sort_keys=False))


if TRACING:
    for _key in ("PUBLIC_KEY", "SECRET_KEY", "BASE_URL"):
        if os.getenv(f"LANGFUSE_{_key}"):
            os.environ.setdefault(f"HERMES_LANGFUSE_{_key}", os.environ[f"LANGFUSE_{_key}"])
    _enable_hermes_plugin("observability/langfuse")

import auth  # noqa: E402
from run_agent import AIAgent  # noqa: E402
from tools.mcp_tool import register_mcp_servers  # noqa: E402

KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# "<provider>:<model>" like the KG agents: ollama:qwen3.6:35b or anthropic:claude-sonnet-5-5.
# Hermes requires a >=64K context window (qwen2.5:32b's 32K is rejected).
HEAD_MODEL = os.getenv("HEAD_MODEL", "ollama:qwen3.6:35b")
HEAD_THINKING = os.getenv("HEAD_THINKING", "false").strip().lower() in ("1", "true", "yes", "on")

# A reply that is nothing but a written-out call like `query_knowledge_graph(domain="x", ...)`.
_TEXT_TOOL_CALL = re.compile(
    r"^\s*`*(query_knowledge_graph|get_entity|ingest_data|get_ontology|update_ontology|list_reviews|"
    r"resolve_review|list_domains|start_research|research_status|list_research|daily_report|list_actions|"
    r"propose_action|list_proposals|list_playbooks|list_sensors|read_sensor|request_sensor|web_search|read_webpage)"
    r"\s*\(.*\)\s*`*\s*$",
    re.S,
)

MCP_PATHS = {"head": "/mcp", "readonly": "/mcp/readonly"}

# Progress events for live UIs: {"type": "tool_start" | "tool_end" | "delta", ...}
EventSink = Callable[[dict], None]


class DomainNotFound(LookupError):
    pass


def connect_kg(domain: str, tools: str = "head") -> str:
    """Connect a domain's Head to the KG's MCP server and register its tools → the toolset name. The connection
    is the service's, limited to this one domain (X-Sovereign-Domain). Idempotent; retries a failed connect."""
    name = f"kg_{tools}_{domain}"
    register_mcp_servers({
        name: {
            "url": f"{KG_URL}{MCP_PATHS[tools]}",
            "timeout": 900,  # ingest_data runs the full LLM pipeline (minutes)
            "connect_timeout": 30,
            "headers": auth.headers(domain),
            # The KG exposes tools only; skip Hermes's resource/prompt helper tools.
            "tools": {"resources": False, "prompts": False},
        }
    })
    return f"mcp-{name}"


def _domain_context(domain: str) -> tuple[str, dict, list[dict]]:
    """Domain description, ontology and action catalog from the KG REST API."""
    with httpx.Client(base_url=KG_URL, timeout=10.0, headers=auth.headers(domain)) as client:
        r = client.get(f"/domains/{domain}")
        if r.status_code == 404:
            raise DomainNotFound(domain)
        r.raise_for_status()
        description = r.json()["config"].get("description") or domain
        ontology = client.get(f"/domains/{domain}/ontology").json()["ontology"]
        actions = client.get(f"/domains/{domain}/actions").json()["actions"]
    return description, ontology, actions


def _system_prompt(domain: str, description: str, ontology: dict, actions: list[dict]) -> str:
    entity_types = ", ".join(ontology.get("entity_types", []))
    relation_types = ", ".join(ontology.get("relation_types", []))
    # The catalog is in the prompt, not behind a tool call: a local model asked to "let the team know" otherwise
    # never looks for the action that does it (measured: it wrote the message into the chat instead).
    catalog = "\n".join(
        f"  - {a['name']}({', '.join(p['name'] for p in a['params'])}): {a['description']}"
        f" [{'proposed — the user confirms' if a['confirm'] else 'runs at once'}]" for a in actions
    )
    return f"""You are the Head Agent of The Sovereign — an autonomous domain expert for the "{domain}" domain.
Domain: {description}

You are the CEO of a knowledge operation. You can ACT through these actions (propose_action):
{catalog}

Your memory is a knowledge graph:
- ENTITIES (types: {entity_types}) are the things this domain is about.
- FACTS connect entities (relations: {relation_types}, plus has_state for a fact about one entity). Each fact
  has a weight from 0 to 1 that grows as independent sources confirm it, an evidence count, and its source
  episodes. Superseded facts carry a `superseded` note — they are history, not current truth.
- Every KG tool takes a `domain` argument. Your domain is always "{domain}".

How you work:
1. BEFORE answering a substantive question, call query_knowledge_graph; use get_entity to see everything known
   about one entity. Never answer domain questions from imagination when the graph can inform you. If the
   graph has nothing relevant, say so plainly.
2. Ground every claim in facts: cite the fact, its weight and evidence count, and its source when it matters.
   Treat weight < 0.5 as a weak signal and say so. Use superseded facts only to explain how things changed.
3. When the user gives you new information worth keeping (or says "ingest this"), call ingest_data and report
   what was stored, strengthened, superseded, rejected or sent to review.
4. Review queue: ambiguous writes wait for you (list_reviews). Decide each with resolve_review and a short
   note: approve a weak fact only if it is worth keeping as a signal; approve a merge only if both names
   are the same real-world thing; approve a link only if the new fact truly restates or replaces the old.
   Use get_entity / query_knowledge_graph to check before deciding.
5. When the graph lacks what a question needs, send the research agent: start_research(domain, question) reads
   the web and every page it reads is ingested into the graph (several minutes, in the background). Tell the
   user you started it; check with research_status / list_research when asked. Don't wait in a loop.
6. For "what happened / what's new / brief me", start from daily_report (written every night after research),
   then drill into the graph if asked. Read it out in your own words; don't just paste it.
7. You alone may change the ontology (update_ontology), and only when accumulated evidence shows the current
   grammar cannot express it (e.g. many facts rejected for the same missing relation). Never for one fact.
8. Acting: when the user asks you to DO something (tell / notify / send / order / create / schedule …) that an
   action above can do, you MUST call propose_action with it — writing the message in the chat is not doing it.
   Also act when what you find calls for it. Check the graph and the playbooks (list_playbooks: responses
   prepared from the graph) first, then call propose_action with a rationale that cites the facts (with
   weights) and their uids as evidence. alert / draft / research run at once. External actions are only PROPOSED: they wait for the user
   to confirm them in the Actions tab — say so, and never claim one was done unless its status is "executed".
   You cannot confirm actions. If the knowledge is weak or stale, say so in the rationale or don't propose.
9. Live data: the graph can be a day old. For what is true NOW (a price, today's news, a status) read a sensor
   (list_sensors / read_sensor), or web_search(recent=true) / read_webpage — always right before proposing an
   action that depends on it. Nothing these return is stored; ingest_data what is worth keeping. When the same
   live need keeps coming up and no sensor covers it, request_sensor.
10. Be decisive and concrete. You act on accumulated knowledge, not on a single snapshot."""


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
    """Entity uids a tool call read or wrote — lets a UI highlight them in the graph."""
    if not isinstance(data, dict):
        return []
    if tool == "ingest_data":
        return list(data.get("touched_uids", []))
    uids = [e["uid"] for e in data.get("entities", []) if "uid" in e]
    if tool == "get_entity" and isinstance(data.get("entity"), dict):
        uids.append(data["entity"]["uid"])
    for f in data.get("facts", []):
        uids += [f.get("source_uid"), f.get("target_uid")]
    return list(dict.fromkeys(u for u in uids if u))


def _progress_callbacks(on_event: EventSink, toolset: str) -> dict:
    """Hermes callbacks → plain JSON-able events (tool names without the MCP prefix)."""
    prefix = f"mcp__{toolset.removeprefix('mcp-')}__"

    def name(tool: str) -> str:
        return tool.removeprefix(prefix)

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


VOICE_MODE = """

VOICE: the user is talking to you out loud and hears your answer read aloud. Answer in at most three short
sentences, in the language the user spoke. No markdown, lists, tables, uids or URLs. Keep numbers, names and
tickers exactly as your tools returned them, written as digits (the voice reads them; don't spell them out).
Lead with the answer. If there is more worth knowing, offer to go deeper. Use your tools exactly as you would
otherwise."""

READ_ONLY = """

READ-ONLY: the user is a viewer of this domain. You can read the graph, reports, playbooks, proposals, sensors and
the web, but you cannot change anything — no ingesting, reviews, ontology changes, research missions or proposed
actions (those tools aren't yours here). When asked for one, say that an editor of the domain has to do it."""


def warm_up(domain: str | None = None) -> None:
    """Load the Head's model (and the domain's embedding model, through one tiny KG query) so the first question
    after a pause doesn't pay for loading them (~20-30 s for a 29 GB local model). Never raises."""
    provider, _, model = HEAD_MODEL.partition(":")
    try:
        with httpx.Client(timeout=120) as client:
            if provider == "ollama":
                client.post(f"{OLLAMA_BASE_URL}/chat/completions", json={
                    "model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1,
                    "reasoning_effort": "none"})
            if domain:
                client.post(f"{KG_URL}/domains/{domain}/query", json={"query": "warm-up", "k": 1},
                            headers=auth.headers(domain))
    except httpx.HTTPError as e:
        print(f"[Hermes] warm-up failed: {type(e).__name__}", flush=True)


def create_head_agent(domain: str, *, max_iterations: int = 8, on_event: EventSink | None = None,
                      voice: bool = False, tools: str = "head") -> AIAgent:
    """Build a domain-scoped Head Agent whose only tools are the KG's MCP tools for that domain
    (`tools`: "head" — everything, for editors; "readonly" — for viewers)."""
    description, ontology, actions = _domain_context(domain)
    toolset = connect_kg(domain, tools)
    return AIAgent(
        **_model_kwargs(),
        enabled_toolsets=[toolset],
        ephemeral_system_prompt=(_system_prompt(domain, description, ontology, actions)
                                 + (READ_ONLY if tools == "readonly" else "") + (VOICE_MODE if voice else "")),
        max_iterations=max_iterations,
        tool_delay=0.0,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,  # Hermes' own memory is one store for everyone: nothing may carry between accounts
        **(_progress_callbacks(on_event, toolset) if on_event else {}),
    )


def ask_head(
    domain: str,
    message: str,
    history: list[dict] | None = None,
    *,
    max_iterations: int = 8,
    on_event: EventSink | None = None,
    voice: bool = False,
    tools: str = "head",
) -> str:
    """One turn with the Head Agent. `history` is prior [{role, content}] turns, oldest first.

    `on_event` (optional) receives live progress events while the agent works.
    """
    agent = create_head_agent(domain, max_iterations=max_iterations, on_event=on_event, voice=voice, tools=tools)
    answer = agent.run_conversation(message, conversation_history=history or None)["final_response"]
    if _TEXT_TOOL_CALL.match(answer or ""):
        # With reasoning off, local models occasionally write a tool call as plain text instead of
        # calling it. Retry once on a fresh agent with a nudge.
        agent = create_head_agent(domain, max_iterations=max_iterations, on_event=on_event, voice=voice, tools=tools)
        nudge = f"{message}\n\n(Call the tools through the tool-calling interface — do not write them as text.)"
        answer = agent.run_conversation(nudge, conversation_history=history or None)["final_response"]
    return answer
