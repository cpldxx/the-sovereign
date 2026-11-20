# Setup

The [README](../README.md#quick-start) has the short version. This page has the rest: the options of the Docker setup, and running the services as processes on your machine for development.

## In Docker (one command)

Requirements: [Docker](https://docs.docker.com/get-docker/), and the models — one of:

| | Local (default) | Claude |
|---|---|---|
| What thinks | `qwen3.6:35b` on [Ollama](https://ollama.com), on this machine | `claude-opus-5-5` through Anthropic's API |
| Machine | ~24 GB of free memory for the model (a 64 GB Apple-silicon Mac is comfortable) | any machine that runs Docker comfortably — no GPU |
| Cost | free | per token — [an API key](https://console.anthropic.com); set a spend limit there |
| Privacy | nothing leaves the machine but searches and page fetches | the pages read and your questions go to Anthropic |

Either way Ollama runs the small embedding model (~1 GB; Anthropic has no embedding API) — on the host, or in a
container with `--ollama`. More in [docs/models.md](models.md).

```bash
git clone https://github.com/cpldxx/the-sovereign.git && cd the-sovereign
./sovereign up            # first time: asks local or Claude (and the API key, hidden), then starts everything
./sovereign up --claude   # Claude without asking
```

`up` writes `.env` with generated secrets and the models you chose, pulls what Ollama is missing (once), builds the
app images, and starts everything: ArcadeDB, SearXNG, crawl4ai, the KG API, Hermes, Research and the web UI. Open
**http://localhost:8000** and create the first account — it is the admin. Then create a domain (a topic): the Head
asks what you want to know, writes a research charter with you, and the lab starts researching.

- `./sovereign init --claude` / `--local` — switch the models of an existing `.env` (then `./sovereign up`)
- `./sovereign up --ollama` — Ollama in a container too (Linux, or the embeddings alone with Claude; on a Mac Docker
  can't use the Apple GPU, so keep a local 35B model on the host)
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

## For development (processes on this machine)

Requirements: Docker, [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node.js. Python 3.12 is installed by uv.

First time — models, config, dependencies:

```bash
ollama pull qwen3.6:35b              # every agent (MoE, fast); thinking off by default
ollama pull nomic-embed-text-v2-moe  # embeddings
# Claude instead: LLM_MODEL / HEAD_MODEL / RESEARCH_MODEL=anthropic:claude-opus-5-5 + ANTHROPIC_API_KEY in kg/.env,
# hermes/.env and research/.env (docs/models.md)

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
