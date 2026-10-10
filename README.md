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
  research · scout · coder · extractor · validator · resolver · linker
  ontologist · strategist · watcher · summarizer · reporter
```

The Head Agent leads twelve agents (`kg/core/crew.py`). It gives them work with `assign_task` (the inputs are
checked by the task's Pydantic model) and asks them questions with `ask_agent` — each answers from what it knows
right now: the Scout from its sensors and sources, the Validator from what it rejected, the Strategist from its
playbooks. Work goes out on one queue (one message at a time: the agents share the local model; long jobs such as a
scout or a research mission run beside it). When an agent finishes, it reports on the thread and the Head is woken
to read the report and decide — store the findings, follow up on the same thread, propose an action. Agents with
news of their own (a playbook fired, the morning report is ready) leave a notice in the Head's inbox. The graph only
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
embedding model — once), builds the sandbox and app images, and starts everything: ArcadeDB, SearXNG, crawl4ai, the
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
far better search than the free engines; free tiers) · `SENSOR_SECRETS` + e.g. `FINNHUB_API_KEY` (kg/.env — sensors can
then use official data APIs instead of unofficial endpoints) · `ANTHROPIC_API_KEY` with `anthropic:` models (per agent).

### Frontend (`frontend/`, port 5173)

One screen per domain: the knowledge graph on the left, tools on the right.

- **Graph** — an Obsidian-style live graph: entities colored by type and sized by their links, faint links that brighten as facts gain evidence, names fading in as you zoom, hover to light an entity and its neighbours, drag to shake it; entities no fact connects yet float as a cloud around it; toggle superseded facts. Click an entity for its facts, history and sources. Refreshes by itself when agents add knowledge.
- **Head Agent** — streaming chat: each KG tool call shows live, and the nodes the agent read or stored light up in the graph.
- **Team** — the Head and its twelve agents at a glance: who is working or queued, and every conversation between them (tasks, questions, answers, reports, the Head's decisions), live.
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
- **Sensors have many sources, and a reading always ends in an answer or an honest "not found".** Each read climbs
  a ladder until something answers:
  1. *parsers* — sources the Scout wrote code for (an API, or a web page opened in a real browser): fast and cheap;
  2. *pages read by the model* — when a source has no working parser (layout changed, none could be written), the model
     reads the rendered page; every value must come with the exact text it was read from, and code checks that this
     text is on the page and contains that number, so a value can't be made up;
  3. *a live search* — when no stored source answers, the web is searched now, the top pages are read the same way,
     and the answer says how many pages agreed;
  4. *"not found", with the reasons* — never a guess; the sensor is scouted again within hours.
  Whichever source answers, the reading is checked against the sensor's fields (pydantic: numbers parsed from "$1,234"
  or "−0.9 %", required fields present, no page markup in text), and a value that moved surprisingly since the last
  reading is confirmed by a second source from another site (a third settles a disagreement).
- **The Scout finds the sources.** For a need it collects real URLs from search hits, the catalog (below) and its own
  list, opens each under Sovereign's rules, has the model confirm the value is really there, writes a parser anchored
  on that text and tests it on two example values (a parser fitted to one ticker breaks on the next) — keeping the page
  as a model-read source when no parser works — until four sources work. Values are voted on with robust statistics
  (median absolute deviation, not a fixed tolerance): a source that read a previous close stands out, spread-out values
  like headline counts aren't voted on. A need nothing could be found for still gets a sensor, answered by live search.
  Measured: stock price 1 → 9 sources; exchange rate 3 (4 on a reworded need, reusing what was learned), crypto 2,
  gold 2.
- **It maintains itself.** Every night each source is read once: a source whose site changed (two failures that aren't
  refusals) gets a new parser written from its current page; one that refuses us (bot protection, 403/429) rests 6 h,
  doubling to 48 h; robots.txt saying no twice retires it; a sensor with fewer than two working sources is scouted again
  (within hours when none works). The **catalog** (`kg/.scout-catalog.json`) learns which URL templates work for which
  needs — credited and debited by every scout and health check, forgotten after three failures, reused for similar
  needs worded differently (embedding similarity) — and which sites refused us lately, so they aren't knocked on again.
- **robots.txt is enforced inside the sandbox** for every request a sensor makes (each redirect too), whoever wrote it;
  pages are opened by the research service, which checks it as well. Bot protection, logins and paywalls are never
  worked around. A live trigger must measure the situation's own quantity and entity — a "$1 trillion market cap"
  situation can't be wired to a share price, nor an NVIDIA situation to AMD's.
- **Sensors run in a locked sandbox** (`docker/sandbox`): a fresh container per run — read-only, non-root, no
  capabilities, memory/CPU/process limits — and an egress guard so code can only reach public internet addresses, never
  this machine's services. Sensor code is statically checked (whitelisted imports, no eval/exec/file access) and never
  runs inside the KG API.
- **Roles are endpoints.** `/mcp` serves the Head everything; `/mcp/readonly` serves reading tools only (e.g. to point
  another assistant at your graph safely).

### Safeguards that check themselves

- **Tests** (`kg/tests`, `./sovereign test`): roles and the auth middleware, sensor code rules, the group shape, routing,
  robust voting, the catalog, the anti-hallucination check — and security invariants against the real pieces:
  webhooks never reach private addresses, sign-up / throttling / revocation in a throwaway accounts database, sensor
  code can't reach this machine or leak a secret, nothing answers without credentials, a domain-scoped connection
  can't leave its domain. Slow ones (`test all`): the model's trigger judgments on known cases, robots.txt enforcement
- **Self-check** every night before research (`POST /system/selfcheck`): sandbox egress, robots.txt, webhooks, auth,
  domain scope, static code check on the live system — a failure becomes a critical alert in every domain
- **Git hooks** (`./sovereign hooks`): a commit with a secret key, `.env`, a data file, compiled `.js` next to the
  sources or a co-author line is refused; KG changes must pass the tests

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
| `GET` | `/domains/{domain}/team` | The Head's agents: role, tasks (inputs), state (working / queued / idle), the Head's unread count |
| `GET` | `/domains/{domain}/threads`, `/threads/{uid}` | Conversations between the Head and its agents (`?agent=`) / one with every message |
| `GET` | `/sensor-requests/{id}` | A sensor request: status, every candidate source and its outcome, live log |
| `GET` | `/health` | KG API + ArcadeDB status, tracing on/off |
| MCP | `/mcp` (Head) | `list_domains`, `query_knowledge_graph`, `get_entity`, `ingest_data`, `get_ontology`, `update_ontology`, `list_reviews`, `resolve_review`, `daily_report`, `list_playbooks`, `list_actions`, `propose_action`, `list_proposals`, `web_search`, `read_webpage`, `list_sensors`, `read_sensor`, `research_status`, `list_research`, `list_agents`, `assign_task`, `ask_agent`, `inbox`, `read_thread` |
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
| Sensors | Scout (many sources, pydantic-ai), built-in coder or OpenHands (`SENSOR_BACKEND`); pages via crawl4ai; pydantic-checked readings | MIT |
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
