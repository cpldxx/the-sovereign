# Roadmap

What has been built, phase by phase, and what comes next.

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

## ⏳ Phase I — Proof
Before more features: find out whether the graph earns its keep, and make the exam a real measurement. The work
goes top to bottom, one small step at a time. `[x]` is done (with the date); a step marked *(owner)* needs a person:
running the lab, labeling, or a decision.

### I1 · An exam hold-out that holds
- [ ] Exam sources leave out pages the graph has read, and near-duplicates of them, not only the same URL
- [ ] Research's "already read" check fails closed when the KG can't be reached

### I2 · Baselines on the same exam
- [ ] Exam answerers are pluggable; the graph is one of them
- [ ] Closed book: the model alone
- [ ] Plain RAG over the stored pages, with the same token budget as the graph's context
- [ ] Live search at question time, without the graph
- [ ] One-shot deep research reports on the same topic
- [ ] One command runs every answerer on a sitting and writes the comparison
- [ ] *(owner)* Run it on a real domain; the results go under Measured

### I3 · A progress check
- [ ] Warn when a domain has had no new page or exam for a set time, not only when a service is down
- [ ] Shown in the app and the briefing

### I4 · A fact audit
- [ ] A stratified sample of stored facts (relation type, weight band, superseded, forgotten), exported
- [ ] A labeling view in the app: correct / wrong / unsure, with the source page beside the fact
- [ ] Precision per band from the labels
- [ ] *(owner)* Label about 200 facts

### I5 · The exam as a measurement
- [ ] A frozen, dated test pool that never steers research, kept apart from the questions that do
- [ ] The grader can be another model family (env), recorded with each sitting
- [ ] More questions, repeated sittings, scores with confidence intervals
- [ ] Questions carry dates; false-premise and unanswerable questions
- [ ] Past graph snapshots re-sit the same pool: a learning curve
- [ ] *(owner)* Grade a sample of answers by hand

### I6 · The Head as people meet it
- [ ] Record per answer whether the Head read the graph before answering
- [ ] Check a sample of answers: does each cited fact support its claim

### I7 · Confidence that knows copies
- [ ] Group near-duplicate pages; count each group, and each site, once
- [ ] Damp the evidence of many weak sources
- [ ] Calibrate weights on the I4 labels (Platt or isotonic) and publish the reliability diagram

### I8 · When facts were true
- [ ] `valid_from` / `valid_to` on facts, read from the text where it says
- [ ] "As of date X" questions in queries and in the exam
- [ ] Supersession precision and recall on labeled pairs

### I9 · Safer forgetting
- [ ] Re-check a fact's source before forgetting it
- [ ] Archive entities instead of deleting them
- [ ] *(owner)* Measure how often forgetting removed a true fact (I4 labels)

### I10 · Steadier extraction on the local model
- [ ] Extraction recall on a fixed set, and run-to-run agreement, against cost
- [ ] Try atomic facts and a two-pass ensemble; keep only what measurably helps

### I11 · Housekeeping
- [ ] Pin the `:latest` Docker images
- [ ] Remove what is left of the removed acting layer
- [ ] Record the token cost of one night in Claude mode
- [ ] *(owner)* A read-only public demo lab with its exam curve

### I12 · A comparison in the open
- [ ] HippoRAG 2 and Graphiti on the same pages and exam, with their own settings; the configs published

### I13 · One user, one promise
- [ ] *(owner)* Choose the first user and the one-line promise; the README and the site follow
