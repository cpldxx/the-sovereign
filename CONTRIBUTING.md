# Contributing

Thanks for wanting to help. Issues and pull requests are welcome — bugs, docs, new source adapters, better
extraction, a cleaner UI.

## Set up

Follow *For development* in the [setup guide](docs/setup.md#for-development-processes-on-this-machine): Docker for the
databases and search, the three Python services with [uv](https://docs.astral.sh/uv/), the UI with Node.js. Then:

```bash
./sovereign hooks      # once: the git hooks (no secrets, local config or data files in a commit; fast KG tests)
./sovereign start      # everything, detached
./sovereign test       # the fast tests (`test all` adds the slow ones: the model, the internet)
```

Models: local (Ollama) or Claude — see [docs/models.md](docs/models.md). Tests that need a live model or the
internet are marked `slow`/`live` and skipped by default.

## Making a change

- **One change per pull request**, with a test when it changes behaviour (`kg/tests/`).
- **Say why in the code.** Comments explain the reason a line exists — what broke, what was measured, and when —
  not what the line does.
- **Measure before you claim.** A change meant to make the lab smarter is tuned on the *practice* questions, then
  the exam is sat once; never tune on the exam itself.
- **Respect the sources.** Research never bypasses robots.txt, logins, paywalls or bot checks; it identifies
  itself honestly and paces each site. Pull requests that weaken this won't be merged.
- **Examples stay neutral.** Docs and prompts use everyday or scientific topics as examples.
- **Commits:** a short English subject line (`Reread pages left pending by a restart`).

## Layout

| Path | What |
|---|---|
| `kg/` | the KG API: ingest pipeline, Graph RAG, ontology, exam, learning (FastAPI + MCP, Pydantic AI agents) |
| `research/` | research service: DeerFlow, search, fetch, papers; pages go to the KG as episodes |
| `hermes/` | the Head agent on Hermes Agent, voice |
| `frontend/` | React + Vite UI |
| `docker/`, `docker-compose.yml` | the stack; `./sovereign up` runs it |

## Reporting security issues

Please don't open a public issue — see [SECURITY.md](SECURITY.md).
