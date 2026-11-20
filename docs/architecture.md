# Architecture

How the parts fit, the Head and its team, the knowledge graph, ingest, query and the ontology; then the safeguards, accounts and isolation, and the stack. Setup is in [setup](setup.md), the routes in [API](api.md).

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

## The Head and its team

```
                 ┌──────────┐
       ┌────────▶│   Head   │◀──────────┐   reports come back on the thread;
       │         └────┬─────┘           │   the Head is woken to read them
       │   tasks,     │ FACTS only      │
       │   questions  ▼ (validated)     │
       │   (queue) ┌──────────────────┐ │
       │           │ Knowledge graph  │ │
       │           └──────────────────┘ │
       │              ▲          ▲      │
       ▼              │          │      │
   Researcher        Learning — the learning pipeline
   (DeerFlow)        extract · validate · resolve · link · ontology · plan · summaries · briefing
```

Three roles (`kg/core/crew.py`): the **Head** (Hermes) talks to you and decides, the **Researcher** (DeerFlow) reads
the web, and **Learning** — the learning pipeline — turns what is read into checked facts and keeps the graph in
shape. The pipeline's steps are its stations; the agent view lights the one at work. The Head gives work with
`assign_task` (the inputs are checked by the task's Pydantic model) and asks questions with `ask_agent` — the
Researcher answers from its runs, Learning from what it read, rejected and is unsure about. Work goes out on one queue
(one message at a time: everything shares the local model; long jobs such as a research mission or a relink run beside
it). When a teammate finishes, it reports on the thread and the Head is woken to read the report and decide — store
the findings, follow up on the same thread, send the Researcher further. Other news (the briefing is ready) is a
notice in the Head's inbox. The graph only ever receives facts that the pipeline checked against their source; the
conversations stay in the threads (click a teammate on the left). Guard: a thread wakes the Head at most 4 times and a
domain 12 times an hour (`HEAD_MAX_WAKES`, `HEAD_WAKES_PER_HOUR`; `HEAD_WAKE=off` leaves every report in the inbox).
Until 2026-10-28 each station was a teammate of its own — nine names for what is one pipeline; their old names still
reach Learning.

- **Agents never touch the database.** Every KG read and write goes through the KG API, which is where ontology checks and validation are enforced — in code, not in prompts.
- **REST is the core; MCP is a thin adapter.** The same functions back both. Hermes discovers the KG tools over MCP with no glue code.
- **Each part has its own dependencies.** Hermes pins its dependencies exactly, so it lives in its own venv and never shares one with the KG.

## The knowledge graph: neurons, synapses, memories

```
(Entity)  neuron   one real-world thing — TSMC, CoWoS, HBM3E — with its aliases and summary
  │ fact  synapse  "TSMC plans to triple CoWoS capacity in 2026" — weight grows as independent
  ▼                sources confirm it; superseded facts are kept as history, not deleted
(Entity)
(Episode) memory   the raw source text it all came from, linked to every entity it mentions
```

## Ingest (usually 2 LLM calls + 1 embedding call per ~8k chars)

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

## Query (Graph RAG)

```
Question → entity + fact vector search → strongest facts around them → their sources
```

## The ontology

The ontology is the grammar of a domain's KG — which node categories and relationships exist. It is generated once when a domain is created and is **enforced in code**: anything outside it never enters the graph. Only the Head Agent may change it.

```json
{
  "entity_types": ["battery_chemistry", "material", "manufacturer", "research_group", "performance_metric"],
  "relation_types": ["uses_material", "develops", "improves_metric", "supplies"]
}
```

## Safeguards that check themselves

- **Tests** (`kg/tests`, `./sovereign test`): roles and the auth middleware, the ingest gates, the crew and its wake
  guard, learning (schedule, forgetting), storage — and security invariants against the real pieces: sign-up /
  throttling / revocation in a throwaway accounts database, nothing answers without credentials, a domain-scoped
  connection can't leave its domain. Slow ones (`test all`) call the model and the internet
- **Self-check** once a day (`POST /system/selfcheck`): robots.txt, auth and domain scope on the live system — a
  failure is left in the Head's inbox in every domain
- **Git hooks** (`./sovereign hooks`): a commit with a secret key, `.env`, a data file, compiled `.js` next to the
  sources or a co-author line is refused; KG changes must pass the tests

## Accounts and isolation (multi-tenant)

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

## Tech stack

| Component | Technology | License |
|---|---|---|
| Agent framework | Hermes Agent (Nous Research) | MIT |
| Knowledge graph + vector DB | ArcadeDB (Cypher over HTTP) | Apache 2.0 |
| Schema enforcement | Pydantic AI 2.x | MIT |
| API + MCP | FastAPI + MCP Python SDK | MIT |
| LLMs + embeddings | Ollama (qwen3.6:35b, nomic-embed) by default; Claude (`claude-opus-5-5`) with `--claude` | — |
| Frontend | React 19 + Vite + Tailwind + force-graph (d3-force, canvas — an Obsidian-style graph) | — |
| Research / collection | DeerFlow 2.1 + SearXNG + crawl4ai | MIT / AGPL-3.0 (run unmodified as a separate service) / Apache 2.0 + attribution |
| Observability | LangFuse (self-hosted, optional) | MIT (core) |
| Voice | mlx-whisper / faster-whisper (local STT), macOS voices or Hermes TTS providers | MIT |
