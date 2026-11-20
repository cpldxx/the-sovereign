# API

Every service's routes. Every endpoint but `/health` and the sign-in ones needs a session cookie or an API token.

## KG API (`kg/`, port 8080)

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
| `POST` | `/domains/{domain}/ingest` | Ingest `{raw_text, source, title?, content_status?, tier?}` → what was created / strengthened / superseded / sent to review |
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
| `GET` / `POST` | `/domains/{domain}/exam` | The exam (its questions), the latest sitting with every answer, the score history / sit it now `{wait?}` (skipped without a charter) |
| `GET` / `POST` | `/domains/{domain}/synthesis` | The living review: a page per core question with its cited points and key sources / rewrite the changed pages `{wait?, force?}` |
| `GET` | `/domains/{domain}/team` | The Head's agents: role, tasks (inputs), state (working / queued / idle), the Head's unread count |
| `GET` | `/domains/{domain}/threads`, `/threads/{uid}` | Conversations between the Head and its agents (`?agent=`) / one with every message |
| `GET` | `/health` | KG API + ArcadeDB status, tracing on/off |
| MCP | `/mcp` (Head) | `list_domains`, `query_knowledge_graph`, `get_entity`, `ingest_data`, `get_ontology`, `update_ontology`, `list_reviews`, `resolve_review`, `daily_report`, `draft_charter`, `confirm_charter`, `suggest_charter`, `exam_results`, `take_exam`, `read_synthesis`, `web_search`, `search_papers`, `read_webpage`, `research_status`, `list_research`, `list_agents`, `assign_task`, `ask_agent`, `inbox`, `read_thread` |
| MCP | `/mcp/readonly` | The reading tools only (no ingest, ontology, review or research requests) |

Every endpoint but `/health` and the sign-in ones needs a session cookie or `Authorization: Bearer <API token>`;
`/mcp` needs editor in the domain a tool touches, `/mcp/readonly` viewer.

## Research (`research/`, port 8070)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/research` | Queue a run `{mode: bootstrap \| update \| mission, question?}` |
| `GET` | `/papers?q=&limit=` | Scholarly papers now (OpenAlex, PubMed, arXiv) with abstracts — for the KG's exam and the Head's `search_papers` (service only) |
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

## Hermes (`hermes/`, port 8090)

| Method | Path | What |
|---|---|---|
| `POST` | `/domains/{domain}/ask` | One turn with the Head Agent `{message, history?}` → `{answer}` |
| `POST` | `/domains/{domain}/ask/stream` | Same, as Server-Sent Events: `tool_start`, `tool_end` (with the node `uids` it read/stored), `delta`, then `answer` or `error`. `voice: true` → short answers meant to be spoken |
| `POST` | `/voice/transcribe` | Speech → text on this machine (body: the audio — webm, wav, mp4, ogg) |
| `POST` | `/voice/speak` | Text → speech (`{text}` → audio; markdown, URLs and uids left out; voice follows the language) |
| `POST` | `/voice/warm?domain=` | Load the speech model, the Head's LLM and the embedding model before the first spoken question |
| `GET` | `/health` | Hermes status + Head model |
