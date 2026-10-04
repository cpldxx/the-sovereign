# The Sovereign

> A self-organizing AI system that builds domain expertise over time — and acts on it.

---

## What Is This?

The Sovereign is a multi-tenant platform where users create **domain-specific AI workspaces**. Each domain gets its own knowledge graph and an agent hierarchy that continuously collects, validates, and stores knowledge into it — then reasons over that graph to take real-world actions.

Think of it as building a domain expert from scratch. You give it a topic. It researches, learns, organizes, and eventually makes decisions — getting smarter every day.

---

## Why Not Just Use ChatGPT or OpenAI Operator?

| | ChatGPT / Operator | The Sovereign |
|---|---|---|
| Memory | None — starts fresh every time | Persistent knowledge graph, grows over time |
| Knowledge | General, hallucination-prone | Domain-specific, validated, structured |
| Action basis | "What I see right now" | "What I've learned over months" |
| Expertise | Generalist | Domain expert that deepens over time |

**Example (Quant Trading domain):**
```
Operator:   "Buy 100 AAPL" → executes

Sovereign:  KG has 6 months of market data, news, patterns
            "RSI at 72 (overbought) + bearish Fed news +
             same pattern in 2022 caused -12% drop.
             Recommend waiting. Here are 3 data points."
            → User confirms → Action Agent executes
```

The gap widens as the knowledge graph grows deeper.

---

## Architecture

"Sovereign" is the whole system — three independent parts:

```
┌──────────────────────── Sovereign ────────────────────────┐
│                                                           │
│  frontend/  — graph, Head Agent chat, ingest, search, ontology│
│        │ REST                         │ REST (/ask)       │
│        ▼                              ▼                   │
│  kg/  Sovereign KG API  ◀── MCP ──  hermes/  Hermes agents│
│   - query (vector + graph)            Head Agent (CEO)    │
│   - ingest pipeline                   (Data / Analyst /   │
│   - ontology, domains                  Coder / Action next)│
│        │                                                  │
│        ▼                                                  │
│  ArcadeDB — one database per domain                       │
│   graph (nodes/edges) + vectors (embeddings)              │
└───────────────────────────────────────────────────────────┘
```

- **Agents never touch the database.** Every KG read and write goes through the KG API, which is where ontology checks and validation are enforced — in code, not in prompts.
- **REST is the core; MCP is a thin adapter.** The same functions back both. Hermes discovers the KG tools over MCP with no glue code.
- **Each part has its own dependencies.** Hermes pins its dependencies exactly, so it lives in its own venv and never shares one with the KG.

### Ingest pipeline (3 LLM calls + 1 embedding call, regardless of node count)

```
Raw text
  → Ingestor     (1 LLM call)  extract all facts as nodes; uid assigned in code
  → Ontology gate (code)       categories outside the ontology are rejected
  → Gatekeeper   (1 LLM call)  validate all nodes, adjust reliability
  → Embedder     (1 call)      embed accepted nodes
  → ArcadeDB                   store nodes + vectors
  → Architect    (1 LLM call)  discover edges; only ontology relations are kept
```

### Query (Graph RAG)

```
Question → embed → vector search (k closest nodes) → expand 1 hop through the graph
```

### The ontology

The ontology is the grammar of a domain's KG — which node categories and relationships exist. It is generated once when a domain is created and is **enforced in code**: anything outside it never enters the graph. Only the Head Agent may change it.

```json
{
  "entity_types": ["crypto_asset", "macro_indicator", "policy_action", "systemic_liquidity", "market_regime"],
  "relation_types": ["controls_liquidity_supply", "shapes_market_regime", "drives_valuation_trend"]
}
```

---

## Quick Start

Requirements: Docker, [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node.js. Python 3.12 is installed by uv.

Run each step from the repo root, steps 2–4 each in their own terminal.

```bash
# 0. Models (every model is an env var — see kg/.env.example and hermes/.env.example)
ollama pull qwen3.6:35b              # every agent (MoE, fast); thinking off by default
ollama pull nomic-embed-text-v2-moe  # embeddings
# Claude instead: set LLM_MODEL / HEAD_MODEL=anthropic:<model> + ANTHROPIC_API_KEY in kg/.env and hermes/.env

# 1. ArcadeDB  → Studio at http://localhost:2480 (root / sovereign_pass)
docker compose up -d

# 2. KG API  → http://localhost:8080  (OpenAPI docs at /docs, MCP at /mcp)
cd kg && cp .env.example .env && uv sync && uv run python main.py

# 3. Hermes (Head Agent)  → http://localhost:8090
cd hermes && cp .env.example .env && uv sync && uv run python server.py

# 4. Frontend  → http://localhost:5173
cd frontend && npm install && npm run dev
```

### Frontend (`frontend/`, port 5173)

One screen per domain: the knowledge graph on the left, tools on the right.

- **Graph** — force layout, colored by ontology category, sized by reliability; click a node for its full text, source, reliability and relations. Refreshes by itself when agents add knowledge.
- **Head Agent** — streaming chat: each KG tool call shows live, and the nodes the agent read or stored light up in the graph. Optional voice in (mic) and out (read answers aloud), using the browser's speech APIs.
- **Ingest** — paste text + source; shows what was stored and why anything was rejected.
- **Search** — Graph RAG: closest facts by meaning plus their 1-hop neighborhood, highlighted in the graph.
- **Ontology** — the domain's grammar with per-type counts; edit it or have the Ontologist regenerate it.
- **Status** — KG API / ArcadeDB / Hermes health in the sidebar.

Backend URLs default to localhost; override with `VITE_KG_URL` / `VITE_HERMES_URL` (see `frontend/.env.example`).

---

## API

### KG API (`kg/`, port 8080)

| Method | Path | What |
|---|---|---|
| `GET` | `/domains` | List domain ids |
| `POST` | `/domains` | Create a domain `{name, description}` → `{domain: "<id>"}` (id is derived: `"Quant Trading"` → `quant_trading`). Creates its database and generates its ontology in the background |
| `GET` / `DELETE` | `/domains/{domain}` | Domain config / delete domain and its database |
| `POST` | `/domains/{domain}/ingest` | Run the ingest pipeline `{raw_text, source}` |
| `POST` | `/domains/{domain}/query` | Graph RAG query `{query, k}` → `matches`, `neighbors`, `edges` |
| `GET` / `PUT` | `/domains/{domain}/ontology` | Read / replace the ontology |
| `POST` | `/domains/{domain}/ontology/generate` | Re-generate the ontology (background) |
| `GET` | `/graph/{domain}`, `/graph` | Nodes + edges for visualization |
| `GET` | `/health` | KG API + ArcadeDB status |
| `POST` | `/domains/{domain}/generate-tools` | OpenHands writes domain tools (background job) |
| MCP | `/mcp` | Tools: `list_domains`, `query_knowledge_graph`, `ingest_data`, `get_ontology`, `update_ontology` |

### Hermes (`hermes/`, port 8090)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/ask` | One turn with the Head Agent `{message, history?}` → `{answer}` |
| `POST` | `/domains/{domain}/ask/stream` | Same, as Server-Sent Events: `tool_start`, `tool_end` (with the node `uids` it read/stored), `delta`, then `answer` or `error` |
| `GET` | `/health` | Hermes status + Head model |

---

## Tech Stack

| Component | Technology | License |
|---|---|---|
| Agent framework | Hermes Agent (Nous Research) | MIT |
| Knowledge graph + vector DB | ArcadeDB (Cypher over HTTP) | Apache 2.0 |
| Schema enforcement | Pydantic AI 2.x | MIT |
| API + MCP | FastAPI + MCP Python SDK | MIT |
| LLMs + embeddings | Ollama (qwen3.6:35b, nomic-embed) by default; Claude via env | — |
| Autonomous coding | OpenHands | MIT |
| Frontend | React 19 + Vite + Tailwind + Cytoscape.js | — |
| Deep research (planned) | Deer-flow | — |
| Observability (planned) | LangFuse | — |
| Voice (planned) | Hermes TTS | — |

---

## Roadmap

### ✅ Phase 1 — Foundation
Core schema, ingest pipeline with batch LLM calls, domain templates, OpenHands integration.

### ✅ Phase 2 — Platform Reset
- Repo split into `kg/`, `hermes/`, `frontend/`
- SurrealDB → ArcadeDB (one database per domain, real edge types)
- Vector search + Graph RAG query
- Ontology enforced in code; ontology API
- MCP adapter; Head Agent on Hermes, connected over MCP
- Pydantic AI 2.x
- New frontend: graph + streaming Head Agent chat (with voice) + ingest + search + ontology

### Phase 3 — Visible Product
- Ontology grounded in Deer-flow research (currently LLM-only)
- Deer-flow integration (domain bootstrap + ongoing research)
- Daily report (LangFuse traces → summary)

### Phase 4 — Agent Hierarchy
- Data Agent (scraping, RSS, APIs), Action Agent (real-world execution)
- LangFuse tracing
- Voice interface (Jarvis-style)

### Phase 5 — SaaS Platform
- User auth + multi-tenant
- Per-user domain isolation
