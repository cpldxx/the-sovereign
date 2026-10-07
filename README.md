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

"Sovereign" is the whole system — four independent parts:

```
┌──────────────────────── Sovereign ────────────────────────┐
│                                                           │
│  frontend/  — graph, Head Agent chat, ingest, search, ontology│
│        │ REST                         │ REST (/ask)       │
│        ▼                              ▼                   │
│  research/  DeerFlow ── raw pages ──▶ kg/ (episodes)      │
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

### The knowledge graph: neurons, synapses, memories

```
(Entity)  neuron   one real-world thing — TSMC, CoWoS, HBM3E — with its aliases and summary
  │ fact  synapse  "TSMC plans to triple CoWoS capacity in 2026" — weight grows as independent
  ▼                sources confirm it; superseded facts are kept as history, not deleted
(Entity)
(Episode) memory   the raw source text it all came from, linked to every entity it mentions
```

### Ingest (usually 2 LLM calls + 1 embedding call per ~8k chars)

```
Source text → Episode
  → Extractor   entities + facts (one call)
  → code gates  ontology types, entity really in the text, valid endpoints
  → Validator   each fact supported by the text? reliability? (one call)
  → Resolver    same entity as an existing one? name keys first, LLM only when ambiguous
  → Linker      restates an existing fact → strengthen it; newer value → supersede the old one
  → ambiguous (weak facts, unsure merges/links) → Head review queue
```

### Query (Graph RAG)

```
Question → entity + fact vector search → strongest facts around them → their sources
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

First time — models, config, dependencies:

```bash
ollama pull qwen3.6:35b              # every agent (MoE, fast); thinking off by default
ollama pull nomic-embed-text-v2-moe  # embeddings
# Claude instead: set LLM_MODEL / HEAD_MODEL=anthropic:<model> + ANTHROPIC_API_KEY in kg/.env and hermes/.env

for s in kg hermes research; do cp $s/.env.example $s/.env; (cd $s && uv sync); done
(cd frontend && npm install)

# The services' own token for calls between them (required; it never reaches a browser — people sign in).
TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
for s in kg hermes research; do sed -i '' "s/^SOVEREIGN_TOKEN=.*/SOVEREIGN_TOKEN=$TOKEN/" $s/.env; done
```

Open the UI and create the first account: it is the admin, owns every domain that already exists, and invites
everyone else (Account → Invites; `SIGNUP=open` in kg/.env lets anyone sign up instead).

Then everything runs from one command — detached, so it keeps running after the terminal closes:

```bash
./sovereign start --observability   # docker (ArcadeDB, SearXNG, crawl4ai, LangFuse) + KG, Hermes, Research, UI
./sovereign status                  # what is up
./sovereign logs kg                 # follow a service's log (.logs/)
./sovereign stop                    # stop the app services (docker keeps running)
./sovereign autostart on            # start at login (macOS LaunchAgent; Docker Desktop must start at login too)
```

UI http://localhost:5173 · KG API http://localhost:8080 (`/docs`, MCP at `/mcp`) · Hermes :8090 · Research :8070 ·
ArcadeDB Studio http://localhost:2480 (root / sovereign_pass) · LangFuse http://localhost:3000 (sign up there; you join
the "sovereign" project; the `.env` files carry its local keys).

Optional keys that make it better (all in the services' `.env`): `TAVILY_API_KEY` or `BRAVE_API_KEY` (research/.env —
far better search than the free engines; free tiers) · `SENSOR_SECRETS` + e.g. `FINNHUB_API_KEY` (kg/.env — sensors can
then use official data APIs instead of unofficial endpoints) · `ANTHROPIC_API_KEY` with `anthropic:` models (per agent).

### Frontend (`frontend/`, port 5173)

One screen per domain: the knowledge graph on the left, tools on the right.

- **Graph** — entities colored by type and sized by mentions; edges thicken as facts gain evidence; toggle superseded facts. Click an entity for its facts, history and sources. Refreshes by itself when agents add knowledge.
- **Head Agent** — streaming chat: each KG tool call shows live, and the nodes the agent read or stored light up in the graph.
- **Voice** — press the mic and just talk: local speech recognition (Whisper on the Apple GPU, ~1 s) and local voices (macOS, English and Korean) — nothing leaves this machine. Pauses end your turn; the Head answers in a few spoken sentences (the full answer stays on screen), says "let me check" while it works, and stops when you talk over it. Once a day it opens with the daily briefing and what waits for your confirmation. Optional wake word ("Sovereign, …" / "자비스, …"). External actions are still confirmed by click, never by voice.
- **Ingest** — paste text + source; shows new/known entities and created/strengthened/superseded/rejected facts with reasons.
- **Search** — Graph RAG: closest entities and facts by meaning plus the strongest facts around them, with sources.
- **Review** — the Head review queue: approve/dismiss yourself, or let the Head Agent decide everything.
- **Ontology** — the domain's grammar with per-type counts; edit it or have the Ontologist regenerate it.
- **Report** — the daily briefing: what the graph learned, what changed (superseded and confirmed facts), what needs attention; read it aloud, highlight its entities in the graph, or write one now.
- **Actions** — the fast path. *Inbox*: proposals waiting for your confirmation (rationale, the facts behind it, a dry run of exactly what will happen) plus the activity feed (alerts, drafts, executed and rejected actions). *Playbooks*: the "if this happens, do that" rules written nightly from the graph. *Sensors*: live-data tools, each backed by many sources (APIs and web pages) — read one now, see which source answered and each source's success rate and code, or have the Scout find more sources. *Catalog*: built-in actions and your own external actions (webhooks).
- **Research** — bootstrap, "what's new" and custom missions; each run shows the pages read, what each added to the graph (and how long it took), failures and the report.
- **Status** — KG API / ArcadeDB / Hermes / Research / search + crawler health in the sidebar, plus a link to the LangFuse traces when tracing is on (admins).
- **Accounts** — sign in; Members (header) shares a domain with other accounts as viewer / editor / owner; Account (sidebar) changes the password, makes API tokens for MCP clients and, for admins, invite links.

Backend URLs default to localhost; override with `VITE_KG_URL` / `VITE_HERMES_URL` (see `frontend/.env.example`).

### Acting: propose → confirm → execute

```
slow path (night)   research → graph → report → playbooks evaluated against today's changes → playbooks rewritten
fast path (now)     you ask / a playbook fires → Head checks graph + playbooks + live sensors → propose_action
                    → alert / draft / research run at once; external actions wait for YOUR confirmation
```

- **Agents only propose.** Confirming is a REST call the UI makes for you; no agent tool can confirm or execute an
  external action. A confirmed proposal runs exactly as previewed, once, and expires after 24 h (`PROPOSAL_TTL_HOURS`).
- **External actions are webhooks you configure** (Actions → Catalog): Slack, n8n, Zapier, a broker or ticket API — the
  URL receives the parameters as JSON. If the endpoint understands `dry_run: true`, its answer becomes the preview.
- **Playbooks can watch live data**: a playbook may carry a sensor condition (e.g. AMD `change_percent <= -8`, every
  60 min). The KG checks due conditions around the clock and fires the playbook's action the moment one holds.
- **Sensors have many sources.** The Scout tries 20-30 public sources for a need — APIs, and web pages opened in a
  real browser for sites without one — and keeps every one that works and agrees with the others (a source more
  than 3 % off the median read the wrong number). Reading a sensor uses its best source and falls back to the next;
  a live trigger fires only when a second source confirms it. Every night each source is read once, and a sensor
  left with fewer than two working sources is scouted again. robots.txt, bot protection, logins and paywalls are
  never worked around — such sources are recorded as unavailable. (Measured for a stock price: 11 of 25 sites
  readable.) With no sensor for something, the Head opens a page that shows it (`read_webpage`, a real browser).
- **Sensors run in a locked sandbox** (`docker/sandbox`): a fresh container per run — read-only, non-root, no
  capabilities, memory/CPU/process limits — and an egress guard so code can only reach public internet addresses, never
  this machine's services. Sensor code is statically checked (whitelisted imports, no eval/exec/file access) and never
  runs inside the KG API.
- **Roles are endpoints.** `/mcp` serves the Head everything; `/mcp/readonly` serves reading tools only (e.g. to point
  another assistant at your graph safely).

### Accounts and isolation (multi-tenant)

- **People sign in** (email + password, scrypt; an httpOnly session cookie good on all three services). Scripts and MCP
  clients use personal API tokens (`Authorization: Bearer svk_…`). Sign-up needs an admin's invite link by default.
- **A domain belongs to its members**: viewer (reads, asks the Head — which then gets read-only tools), editor (also
  ingests, researches, reviews, edits the ontology, confirms actions), owner (also shares, configures webhooks,
  deletes). Every request is checked on every service; a domain you aren't a member of answers 404, as if it didn't
  exist. Ids are global (a second "AI Chips" becomes `ai_chips_3f2a`), names are per account.
- **A Head can't leave its domain.** Hermes connects to the KG once per domain, and the KG limits that connection to
  the one domain — no prompt (or web page the Head reads) can make it query another tenant's graph.
- **What other accounts can't use:** the operator's machine and keys. Webhooks reach public addresses only (no
  Ollama, ArcadeDB or the Sovereign APIs behind them; `ALLOW_PRIVATE_WEBHOOKS=true` for a single-user install with
  local automations). The OpenHands Coder (it drives Docker) and the API keys in `SENSOR_SECRETS` serve only domains an
  admin owns; everyone else's sensors come from the built-in Coder and run in the sandbox without keys.
- **Browser safety:** cookie writes must come from `SOVEREIGN_ORIGINS`; repeated wrong passwords are throttled per
  account and per address; signing out or changing the password ends the sessions (Hermes and Research notice within
  15 s). Behind HTTPS set `COOKIE_SECURE=true`.

---

## API

### KG API (`kg/`, port 8080)

| Method | Path | What |
|---|---|---|
| `POST` | `/auth/signup`, `/auth/login`, `/auth/logout` | `{email, password, name?, invite?}` → session cookie. `GET /auth/config` (public): sign-up mode, first account or not |
| `GET` | `/auth/me` | The caller and their role per domain |
| `POST` | `/auth/password` | `{current, new}` — other browser sessions are signed out |
| `GET` / `POST` / `DELETE` | `/auth/tokens`, `/auth/tokens/{uid}` | Personal API tokens `{label}` (shown once) |
| `GET` / `POST` / `DELETE` | `/auth/invites`, `/auth/invites/{uid}` | Admins: single-use sign-up codes (7 days) |
| `GET` | `/domains` | Your domains: id, title, description, your role |
| `POST` | `/domains` | Create a domain `{name, description}` → `{domain: "<id>"}` — you own it (id derived: `"Quant Trading"` → `quant_trading`, plus a short suffix if another account has it). Creates its database and generates its ontology in the background |
| `GET` / `DELETE` | `/domains/{domain}` | Domain config, stats, your role / delete domain and its database (owner) |
| `GET` / `PUT` / `DELETE` | `/domains/{domain}/members`, `/members/{uid}` | Members / share with an existing account `{email, role}` (owner) / remove (owner) or leave (yourself) |
| `POST` | `/domains/{domain}/ingest` | Ingest `{raw_text, source, title?, content_status?}` → what was created / strengthened / superseded / sent to review |
| `POST` | `/domains/{domain}/query` | Graph RAG `{query, k}` → `entities`, `facts`, `episodes` |
| `GET` / `PUT` | `/domains/{domain}/ontology` | Read / replace the ontology |
| `POST` | `/domains/{domain}/ontology/generate` | Re-generate the ontology (background) |
| `GET` | `/graph/{domain}` | Entities + facts (superseded ones marked `valid: false`) |
| `GET` | `/domains/{domain}/entities/{uid}` | One entity: facts (with history) + source episodes |
| `GET` | `/domains/{domain}/episodes` | Ingested sources, newest first |
| `GET` / `POST` | `/domains/{domain}/reviews`, `/reviews/{uid}` | Head review queue / decide `{approve, note}` |
| `GET` / `POST` | `/domains/{domain}/reports`, `/reports/{uid}` | Daily reports / write one now `{hours: 24, wait?}`: refreshes stale entity summaries, then a briefing of the period's changes |
| `GET` / `POST` / `DELETE` | `/domains/{domain}/actions`, `/actions/{name}` | Action catalog / add a webhook action `{name, description, url, params, dry_run}` / remove it |
| `GET` / `POST` | `/domains/{domain}/proposals`, `/proposals/{uid}` | Proposals (`?status=proposed`) / decide one `{approve, note}` — the user's call |
| `GET` / `POST` / `PATCH` | `/domains/{domain}/playbooks`, `/playbooks/cycle`, `/playbooks/{uid}` | Playbooks / evaluate + rewrite `{hours, refresh, wait}` / retire one |
| `GET` / `POST` / `DELETE` | `/domains/{domain}/sensors`, `/sensors/{name}`, `/sensors/{name}/read` | Sensors (groups of sources) / ask for one `{need, backend?, group?}` — the Scout by default; `group`: more sources for that sensor / read one now `{params}` |
| `POST` | `/domains/{domain}/sensors/check` | Read every source once; scout again where fewer than two work (nightly) |
| `GET` | `/sensor-requests/{id}` | A sensor request: status, every candidate source and its outcome, live log |
| `GET` | `/health` | KG API + ArcadeDB status, tracing on/off |
| MCP | `/mcp` (Head) | `list_domains`, `query_knowledge_graph`, `get_entity`, `ingest_data`, `get_ontology`, `update_ontology`, `list_reviews`, `resolve_review`, `daily_report`, `list_playbooks`, `list_actions`, `propose_action`, `list_proposals`, `web_search`, `read_webpage`, `list_sensors`, `read_sensor`, `request_sensor`, `start_research`, `research_status`, `list_research` |
| MCP | `/mcp/readonly` | The reading tools only (no ingest, ontology, review, proposal, sensor or research requests) |

Every endpoint but `/health` and the sign-in ones needs a session cookie or `Authorization: Bearer <API token>`;
`/mcp` needs editor in the domain a tool touches, `/mcp/readonly` viewer.

### Research (`research/`, port 8070)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/research` | Queue a run `{mode: bootstrap \| update \| mission, question?}` |
| `GET` | `/jobs?domain=`, `/jobs/{id}` | Runs: status, pages read (and what each added to the graph), report |
| `GET` | `/search?q=&time_range=` | Live web search for agents (paced SearXNG) |
| `POST` | `/fetch` | Read one page now `{url}` — robots.txt, SSRF guard, pacing, crawl4ai |
| `GET` | `/health` | Model, SearXNG, crawler, nightly schedule |

Every night (03:00, `RESEARCH_NIGHTLY_HOUR`) each domain gets a "what's new" run — or a bootstrap if it never had one — and, once its runs are finished, its daily report and playbook cycle.

Search uses the local SearXNG by default. Its free engines throttle heavy use (searches are paced, and Sovereign never works around a block); for reliable daily research set `TAVILY_API_KEY` or `BRAVE_API_KEY` in `research/.env` (both have free tiers).

### Hermes (`hermes/`, port 8090)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/ask` | One turn with the Head Agent `{message, history?}` → `{answer}` |
| `POST` | `/domains/{domain}/ask/stream` | Same, as Server-Sent Events: `tool_start`, `tool_end` (with the node `uids` it read/stored), `delta`, then `answer` or `error`. `voice: true` → short answers meant to be spoken |
| `POST` | `/voice/transcribe` | Speech → text on this machine (body: the audio — webm, wav, mp4, ogg) |
| `POST` | `/voice/speak` | Text → speech (`{text}` → audio; markdown, URLs and uids left out; voice follows the language) |
| `POST` | `/voice/warm?domain=` | Load the speech model, the Head's LLM and the embedding model before the first spoken question |
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
| Sensors | Scout (many sources, pydantic-ai), built-in coder or OpenHands (`SENSOR_BACKEND`); pages via crawl4ai | MIT |
| Frontend | React 19 + Vite + Tailwind + Cytoscape.js | — |
| Research / collection | DeerFlow 2.1 + SearXNG + crawl4ai | MIT / AGPL-3.0 (run unmodified as a separate service) / Apache 2.0 + attribution |
| Observability | LangFuse (self-hosted, optional) | MIT (core) |
| Voice | mlx-whisper / faster-whisper (local STT), macOS voices or Hermes TTS providers | MIT |

---

## Roadmap

### ✅ Phase 1 — Foundation
Core schema, ingest pipeline with batch LLM calls, domain templates, OpenHands integration.

### ✅ Phase 2 — Platform Reset (+ Phase A/B)
- Repo split into `kg/`, `hermes/`, `frontend/`
- SurrealDB → ArcadeDB (one database per domain, real edge types)
- Vector search + Graph RAG query
- Ontology enforced in code; ontology API
- MCP adapter; Head Agent on Hermes, connected over MCP
- Pydantic AI 2.x
- New frontend: graph + streaming Head Agent chat (with voice) + ingest + search + ontology
- LLM unification: qwen3.6:35b, thinking off, Claude via env (ontology 164 s → 17 s, Head 60–240 s → 17 s)
- Neuron KG: entities, weighted facts with history, episodes, entity resolution, Head review queue

### ✅ Phase C — DeerFlow collection
- `research/`: DeerFlow (embedded) with SearXNG search and a Sovereign fetch tool (robots.txt, per-site pacing, crawl4ai rendering)
- Bootstrap: research → ontology derived from the pages read → pages ingested as the first episodes
- Nightly "what's new" runs; Head-assigned missions through MCP; the raw pages are ingested, never the LLM report
- Unreadable pages (blocked, paywalled, robots.txt) are recorded, never bypassed

### ✅ Phase D — Observability + daily report
- LangFuse traces every LLM call (KG agents, Hermes, DeerFlow) with tokens and latency; every ingest reports per-stage timings
- Ingest keeps more of what it extracts: amounts/dates never become entities, reverse/variant relations are normalized in code (`manufactured_by` → `manufactures`, swapped), facts about dropped values stay as states; what the ontology can't hold is counted and reported
- Nightly: research → stale entity summaries rewritten → daily report (UI tab, read aloud, Head Agent `daily_report` tool)
- Judging agents (Validator, Resolver, Linker) run at a low temperature for consistent decisions; research no longer hangs on parallel searches

### ✅ Phase E — Agents + action (the fast path)
- Roles as MCP endpoints (`/mcp`, `/mcp/readonly`); only the user confirms external actions
- Actions: built-in alert / draft / research, user webhooks; proposals with dry run, executed once, expire in 24 h
- Playbooks written nightly from the graph, triggered by new facts or live sensor conditions
- Sensors: live-data tools backed by many sources (APIs + browser-rendered pages) found by the Scout, cross-checked,
  falling back from source to source, run in a locked sandbox

### ✅ Phase F — Voice
- Local speech in and out (Whisper on the GPU, macOS voices; Hermes TTS providers optional), hands-free turns with barge-in, wake word, spoken answers, daily briefing on start

### ✅ Phase G — SaaS
- Accounts (invite sign-up, sessions, API tokens), domains shared as viewer / editor / owner, every service checks
  every request, Heads limited to their domain, the operator's machine and keys kept from other tenants

---

## Credits

- This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).
- Entity/fact extraction rules are adapted from [Graphiti](https://github.com/getzep/graphiti) by Zep Software (Apache 2.0).
- Research runs on [DeerFlow](https://github.com/bytedance/deer-flow) (MIT); agents on [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT); storage on [ArcadeDB](https://github.com/ArcadeData/arcadedb) (Apache 2.0).
- Web search via a self-hosted, unmodified [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0). If it is ever modified and offered to users over a network, its source must be published.
