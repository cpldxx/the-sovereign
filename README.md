# The Sovereign

> A self-organizing research engine that builds domain expertise over time.

---

## What Is This?

The Sovereign is a multi-tenant platform where users create **domain-specific research workspaces**. Each domain gets its own knowledge graph and a team of agents that keeps researching, validating and storing what it finds — and a Head Agent that answers from that graph, with its sources.

Think of it as a research lab that never stops. You give it a topic; the Head asks what you really want to know and writes a **research charter** with you — your goal, the core questions, what to leave out, how deep. From then on it researches, learns, organizes and forgets what never held up, steering by that charter — getting smarter every day. It doesn't act in the world (no messages, orders or bookings): it finds out what is true and explains it.

---

## Why Not Just Use ChatGPT?

| | ChatGPT | The Sovereign |
|---|---|---|
| Memory | None — starts fresh every time | Persistent knowledge graph, grows over time |
| Knowledge | General, hallucination-prone | Domain-specific, every fact checked against its source |
| Answer basis | "What I see right now" | "What I've learned over months", with sources and how sure it is |
| Expertise | Generalist | Domain expert that deepens over time — and forgets what never held up |

**Example (AI chips domain):**
```
ChatGPT:    "HBM supply is tight." (no sources, no date)

Sovereign:  KG has weeks of reports, filings and news on HBM
            "HBM4 is sold out through 2027 — confirmed by three sources.
             SK hynix holds ~57% of the market; Samsung's HBM4E base die
             has production problems (a single report — a weak signal)."
```

The gap widens as the knowledge graph grows deeper.

---

## Architecture

"Sovereign" is the whole system — four independent parts:

```
┌──────────────────────── Sovereign ────────────────────────┐
│                                                           │
│  frontend/  — agents, graph, Head chat, briefing, settings │
│        │ REST                         │ REST (/ask)       │
│        ▼                              ▼                   │
│  research/  DeerFlow ── raw pages ──▶ kg/ (episodes)      │
│  kg/  Sovereign KG API  ◀── MCP ──  hermes/  Hermes agents│
│   - query (vector + graph)            Head Agent (CEO)    │
│   - ingest pipeline                   (Extractor, Validator, │
│   - ontology, domains, learning        Researcher, Strategist…)│
│        │                                                  │
│        ▼                                                  │
│  ArcadeDB — one database per domain                       │
│   graph (nodes/edges) + vectors (embeddings)              │
└───────────────────────────────────────────────────────────┘
```

### The Head and its team

```
                 ┌──────────┐
       ┌────────▶│   Head   │◀──────────┐   reports come back on the thread;
       │         └────┬─────┘           │   the Head is woken to read them
       │   tasks,     │ FACTS only      │
       │   questions  ▼ (validated)     │
       │   (queue) ┌──────────────────┐ │
       │           │ Knowledge graph  │ │
       │           └──────────────────┘ │
       │              ▲   ▲   ▲   ▲     │
       ▼              │   │   │   │     │
  research · extractor · validator · resolver · linker · ontologist
  strategist · summarizer · reporter
```

The Head Agent leads nine agents (`kg/core/crew.py`). It gives them work with `assign_task` (the inputs are
checked by the task's Pydantic model) and asks them questions with `ask_agent` — each answers from what it knows
right now: the Researcher from its runs, the Validator from what it rejected, the Strategist from what the graph is
unsure or silent about. Work goes out on one queue (one message at a time: the agents share the local model; long
jobs such as a research mission or a relink run beside it). When an agent finishes, it reports on the thread and the
Head is woken to read the report and decide — store the findings, follow up on the same thread, send the
Researcher further. Other news (the morning report is ready) is a notice in the Head's inbox. The graph only
ever receives facts that the pipeline checked against their source; the conversations stay in the threads, which the
**Team** tab shows. Guard: a thread wakes the Head at most 4 times and a domain 12 times an hour (`HEAD_MAX_WAKES`,
`HEAD_WAKES_PER_HOUR`; `HEAD_WAKE=off` leaves every report in the inbox).

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
  → Relate      a "fact about one entity" that names another → the relation it states (one call, only then)
  → Validator   each fact supported by the text? reliability? (one call)
  → only entities a surviving fact is about are stored (not every name a page mentions)
  → Resolver    same entity as an existing one? name keys first, LLM only when ambiguous
  → Linker      restates an existing fact → strengthen it; newer value → supersede the old one
  → ambiguous (weak facts, unsure merges/links) → Head review queue
```

Extraction runs at a low temperature without presence penalty (the model's defaults made two runs agree on under a
third of their facts), and the Validator checks each fact as the edge it becomes. Chunks are read in parallel and
written one at a time per domain; `kg/scripts/bench_ingest.py` measures speed and agreement on fixed pages.

The graph holds facts, so an entity exists only as the end of a fact. The nightly report also prunes the entities a
rejected review or a merge left without any fact (`POST /domains/{domain}/prune`, owner; a dry run by default). Facts
stored as one entity's state that name another are relinked into the relations they state
(`POST /domains/{domain}/relink`, owner; the states stay). Measured on ai_chips before these (2026-10-10): 68% of the
entities were unconnected — 51% had no fact at all (menus, link text, passing mentions), and many relations had been
filed as states ("Samsung is testing its HBM3E with Nvidia" as a state of HBM3E).

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

### In Docker (one command)

Requirements: [Docker](https://docs.docker.com/get-docker/) and [Ollama](https://ollama.com) running on the machine.

```bash
git clone https://github.com/cpldxx/the-sovereign.git && cd the-sovereign
./sovereign up
```

`up` writes `.env` with generated secrets, pulls the models Ollama is missing (`qwen3.6:35b`, ~24 GB, and an
embedding model — once), builds the app images, and starts everything: ArcadeDB, SearXNG, crawl4ai, the
KG API, Hermes, Research and the web UI. Open **http://localhost:8000** and create the first account — it is the admin.

- `./sovereign up --ollama` — Ollama in a container too (a Linux machine; on a Mac Docker can't use the Apple GPU, so
  keep Ollama on the host)
- `./sovereign up --observability` — with LangFuse traces (UI on port 3000; put its keys in `.env`)
- `./sovereign down` — stop it; data stays in `./data` and the database volume
- Settings: `.env` (see `.env.example`) — models (or Claude via `ANTHROPIC_API_KEY`), sign-up mode, search keys,
  `SOVEREIGN_BIND=0.0.0.0` to reach it from other devices (put HTTPS in front, set `COOKIE_SECURE=true`, and add the
  address to `SOVEREIGN_ORIGINS` — the sign-in check and the MCP endpoint accept only the hosts listed there)
- `./sovereign test` and `./sovereign selfcheck` run against whichever mode is up (in Docker: through nginx)
- If Docker's credential helper hangs (a locked keychain), `up` notices within 10 s and pulls the public images
  without it; your Docker config is left as it is

Only the web UI's port is published: nginx serves the UI and forwards `/api/kg`, `/api/hermes` and `/api/research` to
the services (MCP clients: `http://localhost:8000/api/kg/mcp/readonly` with an API token). Voice in Docker uses
faster-whisper on the CPU and Hermes' TTS providers, with the browser's own voice as the fallback; the Apple-GPU voice
is the development mode's.

### For development (processes on this machine)

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
./sovereign test                    # the test suite (77 checks; `test all` adds the slow ones: the model, the internet)
./sovereign selfcheck               # the security self-check on the running system (also nightly)
./sovereign hooks                   # git hooks: no secrets, data files, build leftovers or co-author lines in commits
```

UI http://localhost:5173 · KG API http://localhost:8080 (`/docs`, MCP at `/mcp`) · Hermes :8090 · Research :8070 ·
ArcadeDB Studio http://localhost:2480 (root / sovereign_pass) · LangFuse http://localhost:3000 (sign up there; you join
the "sovereign" project; the `.env` files carry its local keys).

Optional keys that make it better (all in the services' `.env`): `TAVILY_API_KEY` or `BRAVE_API_KEY` (research/.env —
far better search than the free engines; free tiers) · `ANTHROPIC_API_KEY` with `anthropic:` models (per agent).

### Frontend (`frontend/`, port 5173)

One screen per domain: the agents at work on the left, and three tabs on the right — you talk to the Head, read what
happened, and set things up; the rest is the Head's work. Drawn in graphite (one
neutral scale, a bone-white accent, Geist type bundled with the app): state shows as brightness and motion, not color.

Left — what the team is doing:
- **Agents** (the main view) — the Head, the knowledge graph and the team as one picture: the Head on top, the
  graph at the centre with its counts, the nine agents on a dial around it, bracketed by team. An agent at work
  turns white, its ring turns and its line to the graph flows, with what it is doing beside it; every hand-off travels
  its line as a comet whose shape tells the kind (● task · ○ report · ■ tool call · ◆ facts stored). The console below
  lists the hand-offs with live counts of agents working, tasks queued and hand-offs per minute
  (`GET /domains/{domain}/activity`, polled). **Click an agent** for who it is, what it is doing and its conversations
  with the Head (tasks, questions, answers, reports, the Head's decisions); the Researcher also shows its runs —
  bootstrap, "what's new" and missions, each with the pages read, what each added and the report; the Head shows
  every conversation.
- **Knowledge graph** (a tab of the main view, drawn only while open) — the 300 most connected entities by default
  (1000 / all to choose; `GET /graph/{domain}?limit=&include=`), plus whatever the Head just talked about. It is
  an Obsidian-style live graph: entities tinted by type and sized by their links, faint links that brighten as facts gain evidence, names fading in as you zoom, hover to light an entity and its neighbours, drag to shake it; entities no fact connects yet float as a cloud around it; toggle superseded facts. Click an entity for its facts, history and sources. **Search the graph** opens beside it (Graph RAG: closest entities and facts by meaning plus the strongest facts around them, with sources) and lights up what it finds. Refreshes by itself when agents add knowledge.

Right — three tabs:
- **Head** — streaming chat: each KG tool call shows live, what the Head says on the way stays above its answer,
  and the nodes it read or stored light up in the graph. The conversation is kept in the domain's database, per
  person (Hermes saves each question when it arrives and each answer when it is done — a reload mid-answer loses
  nothing; the answer appears when it lands; `GET /domains/{d}/chat`). 📎 adds a source: paste an article or notes with its source, the pipeline learns
  it (new / known entities, created / strengthened / superseded / rejected facts with reasons) and the chat notes it.
- **Voice** (in the Head tab) — press the mic and just talk: local speech recognition (Whisper on the Apple GPU, ~1 s) and local voices (macOS, English and Korean) — nothing leaves this machine. Pauses end your turn; the Head answers in a few spoken sentences (the full answer stays on screen), says "let me check" while it works, and stops when you talk over it. Once a day it opens with the daily briefing. Optional wake word ("Sovereign, …" / "자비스, …").
- **Briefing** — the briefing each learning cycle writes: what the graph learned, what changed (superseded and
  confirmed facts), what needs attention; read it aloud, highlight its entities in the graph, or write one now.
- **Settings** — *Learning*: when the graph learns by itself, how many questions it pursues, how long an unconfirmed
  weak fact may wait (below). *Ontology*: the domain's grammar with per-type counts; edit it or have the
  Ontologist regenerate it. *Review*: the ambiguous writes (weak facts, possible merges and links) — the Head
  decides them by itself every 30 min (`HEAD_REVIEW_EVERY_MINUTES`, a sweep of at most `HEAD_REVIEW_MINUTES`,
  default 10) and every night before the report (at most `NIGHTLY_REVIEW_MINUTES`, default 45 per domain), with its
  notes on a thread; "Head review" makes it decide now, and you can still overrule one.
- **Status** — KG API / ArcadeDB / Hermes / Research / search + crawler health in the sidebar, plus a link to the LangFuse traces when tracing is on (admins).
- **Accounts** — sign in; Members (header) shares a domain with other accounts as viewer / editor / owner; Account (sidebar) changes the password, makes API tokens for MCP clients and, for admins, invite links.

Backend URLs default to localhost; override with `VITE_KG_URL` / `VITE_HERMES_URL` (see `frontend/.env.example`).

### Researching: how the graph grows

```
first       you give a topic → the Head interviews you → a research charter (goal, core questions, in scope, leave
            out, depth) → you agree → the first deep research: a broad first study + one mission per core question
on demand   you ask → the Head checks the graph → what it lacks goes to the Researcher (a mission) → every page read
            is learned through the pipeline → the Head is woken with the report and answers
by itself   a learning cycle at the times you set (Settings › Learning): what's new + the Strategist's questions for
            the graph's gaps → keep what holds, forget what never did → the Head decides the review queue → briefing
```

- **The charter steers everything.** Research missions, the Strategist's questions, the ontology and the Head read
  it; the Head suggests an addition when you keep asking about something it lacks, and you take it or leave it
  (Settings › Charter). It stays yours to edit — or start over with a new interview.

- **A research engine, not an agent that acts.** Nothing is sent, ordered or booked; the Head finds out what is true,
  keeps it as checked facts with their sources, and says how sure it is.
- **What is true now** (a price, today's news) the Head reads live with `web_search` / `read_webpage` — a real browser
  through the research service — and stores only what is worth keeping, through the same pipeline.
- **robots.txt is respected** for every page the research service opens (each redirect too), and it reaches public
  addresses only. Bot protection, logins and paywalls are never worked around.
- **Roles are endpoints.** `/mcp` serves the Head everything; `/mcp/readonly` serves reading tools only (e.g. to point
  another assistant at your graph safely).

### Safeguards that check themselves

- **Tests** (`kg/tests`, `./sovereign test`): roles and the auth middleware, the ingest gates, the crew and its wake
  guard, learning (schedule, forgetting), storage — and security invariants against the real pieces: sign-up /
  throttling / revocation in a throwaway accounts database, nothing answers without credentials, a domain-scoped
  connection can't leave its domain. Slow ones (`test all`) call the model and the internet
- **Self-check** once a day (`POST /system/selfcheck`): robots.txt, auth and domain scope on the live system — a
  failure is left in the Head's inbox in every domain
- **Git hooks** (`./sovereign hooks`): a commit with a secret key, `.env`, a data file, compiled `.js` next to the
  sources or a co-author line is refused; KG changes must pass the tests

### Accounts and isolation (multi-tenant)

- **People sign in** (email + password, scrypt; an httpOnly session cookie good on all three services). Scripts and MCP
  clients use personal API tokens (`Authorization: Bearer svk_…`). Sign-up needs an admin's invite link by default.
- **A domain belongs to its members**: viewer (reads, asks the Head — which then gets read-only tools), editor (also
  ingests, researches, reviews, edits the ontology), owner (also shares, deletes). Every request is checked on every service; a domain you aren't a member of answers 404, as if it didn't
  exist. Ids are global (a second "AI Chips" becomes `ai_chips_3f2a`), names are per account.
- **A Head can't leave its domain.** Hermes connects to the KG once per domain, and the KG limits that connection to
  the one domain — no prompt (or web page the Head reads) can make it query another tenant's graph.
- **What other accounts can't use:** the operator's machine. The research crawler reaches public addresses only (no
  Ollama, ArcadeDB or the Sovereign APIs behind it), and no container holds the Docker socket.
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
| `GET` / `PUT` / `DELETE` | `/domains/{domain}/charter` | The research charter `{goal, questions, scope, exclude, depth}`, its status (none / draft / active), the Head's suggestions and `brief` (the domain as research prompts see it) / edit / start over |
| `POST` | `/domains/{domain}/charter/draft`, `/charter/confirm`, `/charter/suggestions/{uid}` | Draft it from interview notes `{notes}` / confirm it — the first time this starts the first deep research / take or leave a suggestion `{accept}` |
| `GET` | `/domains/{domain}/team` | The Head's agents: role, tasks (inputs), state (working / queued / idle), the Head's unread count |
| `GET` | `/domains/{domain}/threads`, `/threads/{uid}` | Conversations between the Head and its agents (`?agent=`) / one with every message |
| `GET` | `/health` | KG API + ArcadeDB status, tracing on/off |
| MCP | `/mcp` (Head) | `list_domains`, `query_knowledge_graph`, `get_entity`, `ingest_data`, `get_ontology`, `update_ontology`, `list_reviews`, `resolve_review`, `daily_report`, `draft_charter`, `confirm_charter`, `suggest_charter`, `web_search`, `read_webpage`, `research_status`, `list_research`, `list_agents`, `assign_task`, `ask_agent`, `inbox`, `read_thread` |
| MCP | `/mcp/readonly` | The reading tools only (no ingest, ontology, review or research requests) |

Every endpoint but `/health` and the sign-in ones needs a session cookie or `Authorization: Bearer <API token>`;
`/mcp` needs editor in the domain a tool touches, `/mcp/readonly` viewer.

### Research (`research/`, port 8070)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/research` | Queue a run `{mode: bootstrap \| update \| mission, question?}` |
| `POST` | `/domains/{domain}/kickoff` | The first deep research when a charter is confirmed (the KG calls it) `{questions}`: a bootstrap unless the domain has one, then a mission per core question |
| `GET` | `/jobs?domain=`, `/jobs/{id}` | Runs: status, pages read (and what each added to the graph), report |
| `GET` | `/search?q=&time_range=` | Live web search for agents (paced SearXNG) |
| `POST` | `/fetch` | Read one page now `{url}` — robots.txt, SSRF guard, pacing, crawl4ai |
| `GET` | `/health` | Model, SearXNG, crawler, nightly schedule |

**Learning cycles** — each domain grows by itself on the schedule you set in Settings › Learning (times in your
timezone, several a day if you like; default 03:00 in the Mac's timezone). A cycle: a "what's new" run (a bootstrap
for a new domain), plus research questions the Strategist picks from what the graph is unsure or silent about —
facts resting on one weak source, well-connected entities nothing new was heard about for a month, questions you
asked that the Head had to take to the web, words the ontology lacked (`curiosity`, 0–5 a cycle). Once its runs are
done: **forgetting** (a fact from one weak source nobody confirmed in `forget_after_days`, default 30, is retired —
kept as history, never used as an answer; an entity left with nothing else goes; nothing a pending review relies
on), Head review and the briefing, each step on its own (one that fails doesn't skip the rest). Settings › Learning shows the last cycle (questions, facts learned / confirmed / forgotten), what the
graph is unsure about now, and runs one on demand (`POST research /domains/{d}/cycle`). A run that can't reach the KG before its research is done (a timeout — e.g. the Mac swapping) is tried once more 10 min later (`RESEARCH_RETRY_MINUTES`).

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
| Frontend | React 19 + Vite + Tailwind + force-graph (d3-force, canvas — an Obsidian-style graph) | — |
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

### ✅ Phase E — The team
- Roles as MCP endpoints (`/mcp`, `/mcp/readonly`); the Head leads nine agents over a queue and threads
- Learning cycles on your schedule: what's new + the graph's own open questions, then forgetting what never held up
- (2026-10-25) The acting layer — actions, playbooks, live-data sensors and their sandbox — was removed: the
  Sovereign is a research engine. It is in the git history (commit 42729ee and earlier) if it is ever wanted back

### ✅ Phase F — Voice
- Local speech in and out (Whisper on the GPU, macOS voices; Hermes TTS providers optional), hands-free turns with barge-in, wake word, spoken answers, daily briefing on start

### ✅ Phase G — SaaS
- Accounts (invite sign-up, sessions, API tokens), domains shared as viewer / editor / owner, every service checks
  every request, Heads limited to their domain, the operator's machine kept from other tenants

### 🔜 Phase H — The research lab
- ✅ Research charter: the Head interviews you, drafts the charter, and the first deep research starts when you agree;
  every research run steers by it after that
- Growth you can measure: exam questions drawn from the charter, answered on a schedule — a score over time and
  which core questions the graph can answer
- Source tiers: papers (OpenAlex, arXiv, PubMed) > official sources > news > blogs, weighed in every fact
- A second layer: synthesis pages per core question — the consensus, the debates, the open questions, the key
  sources — rewritten as the graph changes

---

## Credits

- This product includes software developed by UncleCode (https://x.com/unclecode) as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai).
- Entity/fact extraction rules are adapted from [Graphiti](https://github.com/getzep/graphiti) by Zep Software (Apache 2.0).
- Research runs on [DeerFlow](https://github.com/bytedance/deer-flow) (MIT); agents on [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT); storage on [ArcadeDB](https://github.com/ArcadeData/arcadedb) (Apache 2.0).
- Web search via a self-hosted, unmodified [SearXNG](https://github.com/searxng/searxng) (AGPL-3.0). If it is ever modified and offered to users over a network, its source must be published.
