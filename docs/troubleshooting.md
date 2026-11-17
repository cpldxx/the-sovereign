# Troubleshooting

First look: `docker compose ps -a` (what is up), `docker compose --profile app logs -f kg` (or `research`, `hermes`),
and `./sovereign selfcheck`.

## Nothing runs after a restart of the machine

The containers come back by themselves (`restart: unless-stopped`) — once Docker does. Docker Desktop has to start
at sign-in: *Docker Desktop › Settings › General › Start Docker Desktop when you sign in*. Learning cycles that were
due while it was down are skipped, not run late; start one by hand: Settings › Learning › *Run a cycle now*.

## The Mac froze or the services stopped overnight

Almost always memory, with local models. The lab's model (~24 GB) and another large model loaded at the same time
(your own Ollama model, an image generator) can fill a 64 GB Mac: swap fills, macOS kills Ollama's model server,
and Docker or the whole machine stops answering. It looks like a code bug — connection errors, an exam called off —
but the cause is the host.

Check:

```bash
sysctl vm.swapusage                      # swap nearly full?
grep -n "signal: killed" ~/.ollama/logs/server.log | tail
ollama ps                                # what is loaded right now
```

What helps:
- The lab already waits while other Ollama models take more than 8 GB (`RESEARCH_OTHER_MODELS_GB`), and it
  re-reads pages a crash interrupted. It can't see memory used outside Ollama: avoid other heavy work while a
  learning cycle runs, or move the cycles to hours the machine is free (Settings › Learning).
- Or run the models on Claude (`./sovereign init --claude`, [models](models.md)): then only the small embedding
  model is local.

## The exam was "called off"

A sitting where most questions couldn't be graded (the model unreachable or overloaded) is called off instead of
scored, so one bad night doesn't show up as a drop to 0. The next cycle sits it again.

## Research reads few pages

- The free search engines behind SearXNG rate-limit. `TAVILY_API_KEY` or `BRAVE_API_KEY` in `.env` (free tiers)
  switches research to that search API.
- Pages behind robots.txt, a login or a paywall are recorded as unreadable and never bypassed — by design.
- PDFs are read directly when the crawler can't render them (up to 30 MB / 60 pages).

## Claude errors

- `401` — `ANTHROPIC_API_KEY` is missing or wrong in `.env`. Run `./sovereign up` again after fixing it.
- `429` / `529` — rate limited or overloaded; each call is retried a few times. If it keeps happening, give the
  cycles fewer questions or raise your rate limit in the Anthropic console.
- `400` naming `temperature`, `thinking` or `tool_choice` — a setting the current Claude models reject. The lab
  doesn't send them; check any `LLM_*` overrides you added.

## Docker hangs pulling images

A stuck credential helper (a locked keychain) makes every pull wait. `./sovereign up` notices within 10 seconds and
pulls the public images without it; your Docker config is left as it is.

## Starting over

`./sovereign down` stops everything; the data stays in `./data` and the ArcadeDB volume. Deleting `./data` and the
volume (`docker compose down -v`) erases every domain — there is no undo.
