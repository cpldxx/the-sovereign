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
import threading
import time
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
TRACING = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")) and \
    os.getenv("LANGFUSE_TRACING", "true").strip().lower() not in ("false", "off", "0", "no")


def _set_hermes_plugin(name: str, on: bool) -> None:
    """Hermes keeps enabled plugins in HERMES_HOME/config.yaml (a volume): switch it both ways, or a plugin turned
    on once stays on (measured: traces kept being sent to a LangFuse that wasn't running)."""
    import yaml

    path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
    config = (yaml.safe_load(path.read_text()) if path.exists() else None) or {}
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    if on != (name in enabled):
        enabled.append(name) if on else enabled.remove(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config, sort_keys=False))


if TRACING:
    for _key in ("PUBLIC_KEY", "SECRET_KEY", "BASE_URL"):
        if os.getenv(f"LANGFUSE_{_key}"):
            os.environ.setdefault(f"HERMES_LANGFUSE_{_key}", os.environ[f"LANGFUSE_{_key}"])
else:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"   # the SDK's own switch, for anything else that loads it
_set_hermes_plugin("observability/langfuse", TRACING)

import auth  # noqa: E402
from run_agent import AIAgent  # noqa: E402
from tools.mcp_tool import register_mcp_servers  # noqa: E402
import tools.mcp_tool as _mcp_tool  # noqa: E402

# Hermes counts every tool error as a server failure and, after 3 in a row, blocks the whole KG server for 60 s.
# A local model's argument slips (a missing field, a misplaced one) are tool errors too: measured 2026-10-13, three
# bad tool calls locked the Head out of every KG tool for the rest of its turn. A really dead server still
# trips it.
_mcp_tool._CIRCUIT_BREAKER_THRESHOLD = 6
_mcp_tool._CIRCUIT_BREAKER_COOLDOWN_SEC = 20.0

KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# "<provider>:<model>" like the KG agents: ollama:qwen3.6:35b or anthropic:claude-sonnet-5-5.
# Hermes requires a >=64K context window (qwen2.5:32b's 32K is rejected).
HEAD_MODEL = os.getenv("HEAD_MODEL", "ollama:qwen3.6:35b")
HEAD_THINKING = os.getenv("HEAD_THINKING", "false").strip().lower() in ("1", "true", "yes", "on")

# A reply that is nothing but a written-out call like `query_knowledge_graph(domain="x", ...)`.
_TEXT_TOOL_CALL = re.compile(
    r"^\s*`*(query_knowledge_graph|get_entity|ingest_data|get_ontology|update_ontology|list_reviews|"
    r"resolve_review|list_domains|research_status|list_research|daily_report|web_search|read_webpage|"
    r"list_agents|assign_task|ask_agent|inbox|read_thread)"
    r"\s*\(.*\)\s*`*\s*$",
    re.S,
)

MCP_PATHS = {"head": "/mcp", "readonly": "/mcp/readonly"}

# Progress events for live UIs: {"type": "tool_start" | "tool_end" | "delta", ...}
EventSink = Callable[[dict], None]


class DomainNotFound(LookupError):
    pass


class KGUnavailable(RuntimeError):
    """The KG's MCP tools could not be registered: the Head would answer from imagination."""


class Stop:
    """A person's way to stop a turn (the Stop button: POST …/ask/stop). Hermes' own interrupt: no tool call starts
    after it, and the loop ends at its next check. A tool call already running (an ingest on the KG side) finishes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agent: AIAgent | None = None
        self.stopped = False

    def bind(self, agent: AIAgent) -> None:
        with self._lock:
            self._agent = agent
            if self.stopped:
                agent.interrupt()

    def __call__(self) -> None:
        with self._lock:
            self.stopped = True
            if self._agent is not None:
                self._agent.interrupt()


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
    toolset = f"mcp-{name}"
    # A failed connect is only logged by Hermes, and the Head then ran with no tools at all — answering from
    # imagination. A cached connection that dropped reconnects in the background: give it a few seconds.
    for _ in range(10):
        if _toolset_tools(toolset):
            return toolset
        time.sleep(0.5)
    raise KGUnavailable(f"The knowledge graph's tools are unavailable ({KG_URL}{MCP_PATHS[tools]} did not answer). "
                        "Try again in a moment.")


def _toolset_tools(toolset: str) -> list[str]:
    from tools.registry import registry  # noqa: PLC0415

    return registry.get_tool_names_for_toolset(toolset)


def _domain_context(domain: str) -> tuple[str, dict, dict, dict]:
    """Domain description, ontology, team and research charter from the KG REST API."""
    with httpx.Client(base_url=KG_URL, timeout=10.0, headers=auth.headers(domain)) as client:
        r = client.get(f"/domains/{domain}")
        if r.status_code == 404:
            raise DomainNotFound(domain)
        r.raise_for_status()
        description = r.json()["config"].get("description") or domain
        ontology = client.get(f"/domains/{domain}/ontology").json()["ontology"]
        team = client.get(f"/domains/{domain}/team").json()
        charter = client.get(f"/domains/{domain}/charter").json()
    return description, ontology, team, charter


def _team(team: dict) -> str:
    """The roster for the prompt: a local model uses what it is shown."""
    lines = []
    for a in team.get("agents", []):
        # Shown as task name + inputs object, not as a call: written like `check(claims, …)` the model put the
        # whole call into `task` (measured).
        tasks = "; ".join(f'"{t["name"]}" with inputs {json.dumps(t.get("example", {}), ensure_ascii=False)}'
                          + (f" (optional: {', '.join(t['optional'])})" if t.get("optional") else "")
                          for t in a["tasks"]) or "questions only"
        state = "" if a.get("state") == "idle" else f" [{a['state']}]"
        lines.append(f"  - {a['name']} — {a['role']}{state}. Tasks: {tasks}")
    return "\n".join(lines)


def _charter_lines(c: dict) -> str:
    lines = [f"  Goal: {c['goal']}", "  Core questions:", *(f"  - {q}" for q in c["questions"])]
    if c.get("scope"):
        lines.append("  In scope: " + "; ".join(c["scope"]))
    if c.get("exclude"):
        lines.append("  Leave out: " + "; ".join(c["exclude"]))
    lines.append(f"  Depth: {c.get('depth') or 'working'}")
    return "\n".join(lines)


def _charter(domain: str, state: dict, editable: bool) -> str:
    """What the user wants from this domain — or, without a charter yet, the interview that finds out."""
    status, c = state.get("status"), state.get("charter")
    if status == "active" and c:
        waiting = len(state.get("suggestions") or [])
        return f"""
THE RESEARCH CHARTER — what the user wants from this domain. It steers all research; answer with it in mind:
{_charter_lines(c)}
When the user keeps asking about something it doesn't cover (twice or more), propose adding it once with
suggest_charter and tell them they can take it in Settings › Charter{f" ({waiting} suggestion(s) already wait)" if waiting else ""}.
Revise it with draft_charter only when the user asks you to change it.
"""
    if status == "draft" and c:
        return f"""
A DRAFT RESEARCH CHARTER waits for the user's agreement — no research has started from it yet:
{_charter_lines(c)}
If the user agrees (yes, start, looks good…), call confirm_charter: it starts the first deep research — tell them
it runs in the background for a while and the graph fills as it reads. If they want changes, call draft_charter
again with your updated notes.
""" if editable else ""
    if not editable:
        return ""
    return f"""
NO RESEARCH CHARTER YET — interview first. You don't know yet what the user really wants from "{domain}".
Like a thesis advisor's first meeting, find out:
- what they want to understand or decide, and why;
- which questions matter most to them;
- what to leave out;
- how deep (the big picture, working knowledge, or expert depth) and which sources they trust.
Ask one or two short questions per turn, building on their answers; three or four rounds are usually enough. If
they ask something about the domain meanwhile, answer it first, then go on. When you understand — or they want
to start — call draft_charter with your notes from the interview (everything they said that matters, in their
words). Show them the draft briefly (the goal and the core questions) and ask whether to start researching; when
they agree, call confirm_charter. Only a person is interviewed: when a teammate's report or a review sweep woke
you, do that work and leave the charter alone.
"""


def _system_prompt(domain: str, description: str, ontology: dict, team: dict, charter: dict | None = None,
                   editable: bool = True) -> str:
    entity_types = ", ".join(ontology.get("entity_types", []))
    relation_types = ", ".join(ontology.get("relation_types", []))
    return f"""You are the Head Agent of The Sovereign — the lead researcher of the "{domain}" domain.
Domain: {description}

You run a research operation: you find out what is true, keep it as checked facts with their sources, and explain
it. You don't act in the world — no messages, orders or bookings; when asked for one, say this is a research engine
and offer what you found instead.
{_charter(domain, charter or {}, editable)}
You lead a TEAM of agents — give them work and talk to them:
{_team(team)}
- assign_task queues work, e.g. assign_task(domain="{domain}", agent="learning", task="check",
  inputs={{"claims": ["…"], "source_text": "…"}}) — `task` is the name only, its fields go in `inputs`. The agent
  reports on a thread when it is done and you are woken to read the report and decide what follows. Long tasks
  take minutes: say you started it, don't wait. If a tool returns an error, read it and fix the call — never
  claim a connection problem the tool didn't report.
- ask_agent(domain, agent, question) asks one of them what it knows right now (what was rejected, which sources
  were read, what the graph is unsure about…). Use thread="…" to follow up in the same conversation.
- inbox(domain) holds their reports, answers and notices you haven't read{f" — {team['unread']} unread now" if team.get("unread") else ""}.
- The graph holds FACTS only. Store what is worth keeping with ingest_data (the learning pipeline checks each
  fact against its source) — never the conversation itself, never your own guesses.

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
2. Ground every claim in facts. Weights, evidence counts and uids are for YOU to judge how sure a fact is — the user
   never sees them: say it in words ("confirmed by three sources", "a single report", "a weak signal" for weight
   < 0.5) and name a source when it matters — with what kind it is (a fact's source_kinds, a source's kind): "a
   peer-reviewed paper", "an official document", "the press", "a blog". A paper or an official source outweighs
   press and blogs. Use superseded facts only to explain how things changed, and say where sources disagree.
3. When the user gives you new information worth keeping (or says "ingest this"), call ingest_data and report
   what was stored, strengthened, superseded, rejected or sent to review.
4. Review queue: ambiguous writes wait for you (list_reviews). Decide each with resolve_review and a short
   note: approve a weak fact only if it is worth keeping as a signal; approve a merge only if both names
   are the same real-world thing; approve a link only if the new fact truly restates or replaces the old.
   Use get_entity / query_knowledge_graph to check before deciding.
5. When the graph lacks what a question needs, send the researcher: assign_task(domain, "research",
   "investigate", {{"question": …}}) reads the web and every page it reads is learned into the graph (minutes, in
   the background; it reports back when done). Tell the user you started it. Don't wait in a loop. For a broad
   question, have Learning plan first (assign_task(domain, "learning", "plan", {{"n": 3}})): which questions would
   close the graph's biggest gaps, the charter's core questions first — then send the researcher on those.
6. For "what happened / what's new / brief me", start from daily_report (written after each learning cycle),
   then drill into the graph if asked. For a broad question about what the domain knows ("what do we know about…",
   "summarize…"), start from read_synthesis — the living review of each core question — then drill in. Read them out
   in your own words; don't just paste them.
7. You alone may change the ontology (update_ontology), and only when accumulated evidence shows the current
   grammar cannot express it (e.g. many facts rejected for the same missing relation). Never for one fact.
8. What is true NOW (a price, today's news, a status) may be newer than the graph: find it with web_search
   (recent=true) and read the page with read_webpage — a real browser, so most news, quote and official pages
   work; if one site refuses, try another. What research has established: search_papers (papers with their
   abstracts). Nothing these return is stored; ingest_data what is worth keeping, with its URL as the source.
9. Be decisive and concrete: say what the evidence supports, how sure it is, and what is still unknown.

How you write to the user:
- In the user's language, and only that language. A question in Korean gets natural Korean from start to end —
  no Chinese characters, no Japanese, no English words where Korean has one; only proper names and product names
  stay as written (SK hynix, TSMC, HBM4).
- No internals: no uids, weights, scores, field or tool names, JSON.
- Call the tools you need first, then write the answer once — don't draft it before a tool call and repeat it."""

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
    # Hermes merges request_overrides into every chat request. qwen3.6 ships with presence_penalty 1.5, which Qwen
    # itself warns "may occasionally result in language mixing": measured 2026-10-13, Korean answers drifted into
    # Chinese and Japanese mid-sentence (年底, 但這, タイトネス). The KG's agents run at 0 for the same model (core/llm.py).
    kwargs["request_overrides"] = {"presence_penalty": 0.0, "temperature": 0.7, "top_p": 0.8}
    if not HEAD_THINKING:
        # Ollama's OpenAI-compatible API turns Qwen3 reasoning fully off with this.
        kwargs["request_overrides"]["reasoning_effort"] = "none"
    return kwargs


VOICE_MODE = """

VOICE: the user is talking to you out loud and hears your answer read aloud. Answer in at most three short
sentences, in the language the user spoke. No markdown, lists, tables, uids or URLs. Keep numbers, names and
tickers exactly as your tools returned them, written as digits (the voice reads them; don't spell them out).
Lead with the answer. If there is more worth knowing, offer to go deeper. Use your tools exactly as you would
otherwise."""

READ_ONLY = """

READ-ONLY: the user is a viewer of this domain. You can read the graph, reports, your team's threads and the web,
but you cannot change anything — no ingesting, reviews, ontology changes, or tasks and questions for the team
(those tools aren't yours here). When asked for one, say that an editor
of the domain has to do it."""


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
    description, ontology, team, charter = _domain_context(domain)
    toolset = connect_kg(domain, tools)
    return AIAgent(
        **_model_kwargs(),
        enabled_toolsets=[toolset],
        ephemeral_system_prompt=(_system_prompt(domain, description, ontology, team, charter, editable=tools == "head")
                                 + (READ_ONLY if tools == "readonly" else "") + (VOICE_MODE if voice else "")),
        max_iterations=max_iterations,
        tool_delay=0.0,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,  # Hermes' own memory is one store for everyone: nothing may carry between accounts
        **(_progress_callbacks(on_event, toolset) if on_event else {}),
    )


# ── What the user reads ────────────────────────────────────────────────────
# The prompt asks for clean, one-language answers; these catch what a local model still lets through.
_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_FOREIGN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")          # kana and Chinese characters
# Uids and weights a model copied from its tools into what a person reads. Until 2026-10-14 only uids starting f_ / e_ / ep_ / thre_ / prop_ / chat_ were caught (r_, rep_,
# play_, sens_ … leaked) and any parenthetical naming a weight went, prose too. Uids have the KG's shape (new_uid:
# 1-4 letters, 12 hex); a parenthetical goes only when it holds nothing but such internals.
_UID = r"`?\b[a-z]{1,4}_[0-9a-f]{12}\b`?"
_PAIR = r"\b(?:weight|evidence)\s*[=:]\s*\d+(?:\.\d+)?"
_PAREN = re.compile(r"\s*[(（]([^()（）]*)[)）]")
_LEFT_OVER = re.compile(r"(?:[\s,;/|]|\b(?:uids?|ids?|facts?)\b[:=]?)*", re.I)


def scrub(text: str) -> str:
    """Remove uids and weights a model copied from its tools into text meant for a person."""
    def paren(m: re.Match) -> str:
        inner = re.sub(_PAIR, "", re.sub(_UID, "", m[1]), flags=re.I)
        return "" if inner != m[1] and _LEFT_OVER.fullmatch(inner) else m[0]
    text = _PAREN.sub(paren, text)
    text = re.sub(r",?\s*" + _PAIR, "", text, flags=re.I)
    return re.sub(r"\s*" + _UID, "", text)


def _korean_only(answer: str) -> str:
    """One rewrite when a Korean answer drifted into Chinese or Japanese (the content stays; on failure, as is)."""
    try:
        r = httpx.post(f"{OLLAMA_BASE_URL.rstrip('/')}/chat/completions", timeout=180, json={
            "model": HEAD_MODEL.partition(":")[2], "stream": False, "temperature": 0.2, "presence_penalty": 0.0,
            "reasoning_effort": "none",
            "messages": [
                {"role": "system", "content": "Rewrite the text in natural Korean only. Replace every Chinese "
                 "character, Japanese word and needless English word with Korean; keep proper names and product "
                 "names (SK hynix, TSMC, HBM4), numbers, markdown and meaning exactly. Output only the rewritten text."},
                {"role": "user", "content": answer}]})
        r.raise_for_status()
        fixed = r.json()["choices"][0]["message"]["content"].strip()
        return fixed if fixed and len(_FOREIGN.findall(fixed)) < len(_FOREIGN.findall(answer)) else answer
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        print(f"[Hermes] Korean rewrite failed: {type(e).__name__}", flush=True)
        return answer


def tidy(message: str, answer: str) -> str:
    answer = scrub(answer or "")
    # The rewrite asks Ollama for HEAD_MODEL by name: only when the Head runs there (an anthropic: Head has no
    # such model on Ollama — the call only failed after a wait).
    if HEAD_MODEL.startswith("ollama:") and _HANGUL.search(message) and _FOREIGN.search(answer):
        answer = scrub(_korean_only(answer))
    return answer


# What of a conversation goes to the Head: the page sends all of it (up to 400 turns), and every turn the Head read
# it all again — a long chat made each answer slower. The newest turns, each cut, within a total.
HISTORY_TURNS = 12
HISTORY_CHARS = 8000
TURN_CHARS = 2000


def recent(history: list[dict] | None) -> list[dict]:
    """The newest turns of `history` (oldest first, as given) within HISTORY_TURNS / HISTORY_CHARS, each cut to
    TURN_CHARS; it starts with a question, not an answer."""
    kept, total = [], 0
    for turn in reversed(history or []):
        content = str(turn.get("content") or "")
        if len(content) > TURN_CHARS:
            content = content[:TURN_CHARS] + " …"
        if len(kept) >= HISTORY_TURNS or total + len(content) > HISTORY_CHARS:
            break
        kept.append({"role": turn["role"], "content": content})
        total += len(content)
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def ask_head(
    domain: str,
    message: str,
    history: list[dict] | None = None,
    *,
    max_iterations: int = 8,
    on_event: EventSink | None = None,
    voice: bool = False,
    tools: str = "head",
    stop: Stop | None = None,
) -> str:
    """One turn with the Head Agent. `history` is prior [{role, content}] turns, oldest first (only the newest are
    used, see recent()).

    `on_event` (optional) receives live progress events while the agent works. `stop` (optional) interrupts it.
    """
    history = recent(history) or None
    agent = create_head_agent(domain, max_iterations=max_iterations, on_event=on_event, voice=voice, tools=tools)
    if stop:
        stop.bind(agent)
    answer = agent.run_conversation(message, conversation_history=history)["final_response"]
    if stop and stop.stopped:
        return scrub(answer or "")
    if _TEXT_TOOL_CALL.match(answer or ""):
        # With reasoning off, local models occasionally write a tool call as plain text instead of
        # calling it. Retry once on a fresh agent with a nudge.
        agent = create_head_agent(domain, max_iterations=max_iterations, on_event=on_event, voice=voice, tools=tools)
        if stop:
            stop.bind(agent)
        nudge = f"{message}\n\n(Call the tools through the tool-calling interface — do not write them as text.)"
        answer = agent.run_conversation(nudge, conversation_history=history)["final_response"]
        if stop and stop.stopped:
            return scrub(answer or "")
    return tidy(message, answer)
