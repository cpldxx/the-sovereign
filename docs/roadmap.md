# Roadmap

What has been built, phase by phase.

## ✅ Phase 1 — Foundation
Core schema, ingest pipeline with batch LLM calls, domain templates, OpenHands integration.

## ✅ Phase 2 — Platform Reset (+ Phase A/B)
- Repo split into `kg/`, `hermes/`, `frontend/`
- SurrealDB → ArcadeDB (one database per domain, real edge types)
- Vector search + Graph RAG query
- Ontology enforced in code; ontology API
- MCP adapter; Head Agent on Hermes, connected over MCP
- Pydantic AI 2.x
- New frontend: graph + streaming Head Agent chat (with voice) + ingest + search + ontology
- LLM unification: qwen3.6:35b, thinking off, Claude via env (ontology 164 s → 17 s, Head 60–240 s → 17 s)
- Neuron KG: entities, weighted facts with history, episodes, entity resolution, Head review queue

## ✅ Phase C — DeerFlow collection
- `research/`: DeerFlow (embedded) with SearXNG search and a Sovereign fetch tool (robots.txt, per-site pacing, crawl4ai rendering)
- Bootstrap: research → ontology derived from the pages read → pages ingested as the first episodes
- Nightly "what's new" runs; Head-assigned missions through MCP; the raw pages are ingested, never the LLM report
- Unreadable pages (blocked, paywalled, robots.txt) are recorded, never bypassed

## ✅ Phase D — Observability + daily report
- LangFuse traces every LLM call (KG agents, Hermes, DeerFlow) with tokens and latency; every ingest reports per-stage timings
- Ingest keeps more of what it extracts: amounts/dates never become entities, reverse/variant relations are normalized in code (`manufactured_by` → `manufactures`, swapped), facts about dropped values stay as states; what the ontology can't hold is counted and reported
- Nightly: research → stale entity summaries rewritten → daily report (UI tab, read aloud, Head Agent `daily_report` tool)
- Judging agents (Validator, Resolver, Linker) run at a low temperature for consistent decisions; research no longer hangs on parallel searches

## ✅ Phase E — The team
- Roles as MCP endpoints (`/mcp`, `/mcp/readonly`); the Head leads its team over a queue and threads
- Learning cycles on your schedule: what's new + the graph's own open questions, then forgetting what never held up
- (2026-10-25) The acting layer — actions, playbooks, live-data sensors and their sandbox — was removed: the
  Sovereign is a research engine. It is in the git history (commit 42729ee and earlier) if it is ever wanted back

## ✅ Phase F — Voice
- Local speech in and out (Whisper on the GPU, macOS voices; Hermes TTS providers optional), hands-free turns with barge-in, wake word, spoken answers, daily briefing on start

## ✅ Phase G — SaaS
- Accounts (invite sign-up, sessions, API tokens), domains shared as viewer / editor / owner, every service checks
  every request, Heads limited to their domain, the operator's machine kept from other tenants

## ✅ Phase H — The research lab
- ✅ Research charter: the Head interviews you, drafts the charter, and the first deep research starts when you agree;
  every research run steers by it after that
- ✅ Three roles: the Head, the Researcher and Learning (the learning pipeline, its steps shown as stations)
- ✅ Growth you can measure: an exam written from held-out authoritative sources (round-trip checked), answered from
  the graph alone after every cycle — a score over time, each core question's state, the open ones researched next
- ✅ Source tiers: papers (OpenAlex, arXiv, PubMed) > official sources > news > blogs, weighed in every fact
- ✅ A second layer: synthesis pages per core question — the consensus, the debates, the open questions, the key
  sources — rewritten as the graph changes
