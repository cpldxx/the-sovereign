# The Sovereign

> A self-organizing AI system that builds domain expertise over time — and acts on it.

---

## What Is This?

The Sovereign is a multi-tenant platform where users create **domain-specific AI workspaces**. Each domain spawns an autonomous agent hierarchy that continuously collects, validates, and stores knowledge into a growing knowledge graph — then reasons over that graph to take real-world actions.

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

## How It Works

### 1. Create a Domain
You give it a name and a short description. That's it.

```
Domain: "Quant Trading"
Description: "Algorithmic trading strategies, technical analysis, market signals"
```

### 2. System Bootstraps Itself (one-time, runs in background)
```
Deer-flow researches the domain
  → reads papers, news, web sources
  → Head Agent builds ontology.json from real data
  → 5 specialized agents spawned based on domain plan
  → System is ready
```

The ontology defines the "grammar" of the knowledge graph for this domain — what entity types exist, what relationships are valid. Built from real research, not LLM imagination.

### 3. Agents Run Autonomously
```
Data Agent    → scrapes web, RSS feeds, papers, APIs (continuously)
Analyst Agent → extracts entities + relationships (ontology-constrained)
Head Agent    → reviews all extractions, approves KG writes
Coder Agent   → writes domain-specific tools via OpenHands
Action Agent  → executes real-world actions on Head's decision
```

Everything is traced via LangFuse. Every 24 hours, a Daily Report summarizes what happened.

### 4. You Talk to It (Voice, Jarvis-style)
Not a chatbot. Voice conversation with the Head Agent, which has full access to the knowledge graph.

```
You:  "What happened today?"
Head: "We added 47 new nodes. RSI patterns for NVDA are showing
       overbought conditions similar to the March 2024 correction..."

You:  "Should I reduce my NVDA position?"
Head: "Based on 3 historical patterns in the KG, probability of
       a 5%+ pullback in the next 2 weeks is high. Recommend reducing
       by 30%. Want me to execute?"

You:  "Do it."
Head: → Action Agent executes the trade
```

---

## Agent Hierarchy

```
User (You)
  └── Head Agent
        ├── Reads entire KG
        ├── Only agent that can modify the ontology
        ├── Final approval on all KG writes
        ├── Talks to you via voice
        └── Coordinates:
              ├── Data Agent      — data collection
              ├── Analyst Agent   — knowledge extraction
              ├── Coder Agent     — writes tools (OpenHands)
              └── Action Agent    — real-world execution
```

Agents run sequentially, not in parallel. Head dispatches → one agent works → returns to Head. This keeps the system coherent and the Head in control.

### LLM per Role
```
Head Agent    → qwen3.6:35b (or Claude API)   — reasoning, decisions
Analyst Agent → qwen2.5:14b                   — structured extraction
Data Agent    → qwen2.5:7b                    — simple fetch + format
Coder Agent   → qwen2.5-coder:32b             — code generation
Action Agent  → small model + tool calls      — execution only
```

---

## The Ontology System

The ontology is the grammar of the knowledge graph. Generated **once** when a domain is created, from real research data.

```json
{
  "entity_types": [
    "technical_indicator",
    "price_pattern",
    "risk_signal",
    "strategy",
    "market_condition"
  ],
  "relation_types": [
    "confirms",
    "contradicts",
    "triggers",
    "requires",
    "correlates_with"
  ]
}
```

Without ontology: same concept gets tagged differently every time → graph becomes inconsistent.

With ontology: every node uses consistent types → graph is queryable, comparable, meaningful.

Only the Head Agent can update the ontology — and only based on evidence from accumulated data.

---

## Knowledge Graph

- **Database**: SurrealDB (native graph, RELATE statements)
- **One namespace per domain** — fully isolated
- **Node**: uid, domain, category, content, source, tags, reliability score
- **Edge**: from_node, to_node, relation type, weight
- **Schema enforced by Pydantic AI** — if LLM output doesn't match schema, it never enters the DB

---

## Ingest Pipeline (3 LLM calls, regardless of node count)

```
Raw data (text, URL, paper, news)
  → Ingestor          (1 LLM call) → extract all nodes at once
  → Gatekeeper        (1 LLM call) → validate all nodes at once
  → Head Agent approval
  → SurrealDB store
  → Architect         (1 LLM call) → discover all edges at once
  → Knowledge graph updated
```

---

## Observability

All agent activity traced via **LangFuse**:
- Which agent called which, with what input/output
- Token usage and latency per agent
- Full conversation traces between agents

**Daily Report** (every 24h):
- LangFuse traces → LLM summarizes → delivered to user via voice
- "Today: 47 nodes added, 3 ontology updates, 2 trade signals identified"

---

## Tech Stack

| Component | Technology |
|---|---|
| Schema enforcement | Pydantic AI |
| Knowledge graph DB | SurrealDB |
| Autonomous coding | OpenHands |
| Deep research + ontology bootstrap | Deer-flow |
| Local LLM | Ollama (qwen family) |
| Production LLM | Claude API (Anthropic) |
| Observability | LangFuse |
| API layer | FastAPI |
| Voice interface | TBD |
| Frontend + graph visualization | TBD (React Flow / Cytoscape.js) |

---

## Roadmap

### ✅ Phase 1 — Foundation (complete)
- Core schema (SovereignNode, SovereignEdge)
- SurrealDB knowledge graph
- Agent pipeline: Ingestor → Gatekeeper → Architect
- Batch processing (3 LLM calls total, not N)
- OpenHands integration (autonomous code generation)
- Domain template system
- End-to-end pipeline confirmed

### 🔨 Phase 2 — Visible Product
- Ontology system (Deer-flow bootstrapped at domain creation)
- Graph API (nodes + edges as JSON)
- Frontend + knowledge graph visualization
- Daily report generation (LangFuse → summary)

### Phase 3 — Agent Hierarchy
- Head Agent (KG-aware, ontology gatekeeper, coordinates all)
- Data Agent (autonomous scraping + collection)
- Action Agent (real-world execution)
- LangFuse full integration
- Voice interface (Jarvis-style)

### Phase 4 — SaaS Platform
- User auth + multi-tenant
- Per-user domain isolation
- Domain management from UI
- Claude API upgrade for Head Agent

---

## Dev Setup

```bash
# Requirements: Docker, Ollama, uv, Python 3.12

# 1. Clone and install
git clone https://github.com/cpldxx/the-sovereign
cd sovereign
uv sync

# 2. Start SurrealDB
docker run -d --name surrealdb -p 8000:8000 surrealdb/surrealdb:latest \
  start --user root --pass sovereign_pass memory

# 3. Pull models
ollama pull qwen2.5:14b

# 4. Configure
cp .env.example .env

# 5. Run
uv run python main.py
# → http://localhost:8080
```

---

## Status

Active development. Phase 1 complete, Phase 2 in progress.
