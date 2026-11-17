# Models: local or Claude

The lab thinks with three language models and one embedding model, each set in `.env`:

| Variable | Who uses it | Local default | Claude |
|---|---|---|---|
| `LLM_MODEL` | the KG's agents — Extractor, Validator, Resolver, Linker, Reporter, Strategist, Examiner… | `ollama:qwen3.6:35b` | `anthropic:claude-opus-5-5` |
| `HEAD_MODEL` | the Head you talk to (Hermes) | `ollama:qwen3.6:35b` | `anthropic:claude-opus-5-5` |
| `RESEARCH_MODEL` | the research agent (DeerFlow: searches, reads, collects pages) | `ollama:qwen3.6:35b` | `anthropic:claude-opus-5-5` |
| `EMBEDDING_MODEL` | vectors for search — always local | `nomic-embed-text-v2-moe` | same |

Every model variable is `<provider>:<model>`. Any mix works: the Head on Claude and the rest local, for instance.

## Switching

```bash
./sovereign init --claude    # all three on Claude (asks for ANTHROPIC_API_KEY once, hidden)
./sovereign init --local     # all three back on Ollama
./sovereign up               # apply
```

Or edit `.env` by hand and run `./sovereign up` again. A first `./sovereign up` asks which you want.

## Local (Ollama)

- **Memory.** `qwen3.6:35b` is a mixture-of-experts model of ~24 GB. A 64 GB Apple-silicon Mac runs it
  comfortably; a 32 GB one works if little else is open. On a Mac keep Ollama on the host (Docker can't use the
  Apple GPU); on Linux with a GPU, `./sovereign up --ollama` runs it in a container too.
- **Sharing the machine.** The lab waits while *other* Ollama models take more than 8 GB
  (`RESEARCH_OTHER_MODELS_GB`), so your own work and the lab don't both load a large model and run the machine out
  of memory. It can't see memory used outside Ollama (an image generator, a game) — see
  [troubleshooting](troubleshooting.md#the-mac-froze-or-the-services-stopped-overnight).
- **Reasoning is off** (`LLM_THINKING=false`): measured on this model, extraction was ~9x faster without it at the
  same quality.
- **Another server.** `LLM_MODEL=openai:<model>` with `LLM_BASE_URL=http://…/v1` points the KG's agents at any
  OpenAI-compatible server (mlx-lm, vLLM, llama.cpp).

## Claude (Anthropic API)

- **Key.** Create one at [console.anthropic.com](https://console.anthropic.com) and put it in `.env` as
  `ANTHROPIC_API_KEY` (`./sovereign init --claude` asks for it). Set a monthly spend limit there.
- **Model.** `claude-opus-5-5` by default. Any Claude model id works in each variable — for example
  `LLM_MODEL=anthropic:claude-sonnet-5-5` or `anthropic:claude-haiku-5-5` for the KG's agents, which make the most
  calls.
- **Effort.** The current Claude models take no temperature and can't turn thinking off; *effort* is the dial. The
  KG's agents run at `LLM_EFFORT=low` (they extract and check; long deliberation doesn't help them). The Head and the
  research agent use the model's default.
- **What it costs** depends on how much the lab reads. Each page read is several model calls (a chunk of ~8k
  characters takes about two), a research mission reads around ten pages, and a nightly cycle runs a few missions,
  an exam and a report. Watch the usage page in the Anthropic console for the first few cycles. To spend less:
  fewer questions per cycle or fewer cycles (Settings › Learning), or a smaller model for `LLM_MODEL`.
- **Privacy.** The pages the lab reads, the graph's facts it reasons over, and your questions are sent to
  Anthropic. Local models keep them on the machine.
- **Embeddings still need Ollama** (~1 GB, CPU is fine): the host's Ollama, or `./sovereign up --claude --ollama`
  for one in a container.

## Embeddings

`EMBEDDING_MODEL` is served by Ollama by default; `EMBEDDING_BASE_URL` points it at any OpenAI-compatible
`/embeddings` endpoint instead. Changing the embedding model usually changes the vector size: a domain built with
the old one rejects new vectors until it is re-embedded, so pick it before you build a large graph.

## Per-agent overrides

`LLM_MODEL_<AGENT>` overrides `LLM_MODEL` for one agent: `EXTRACTOR`, `VALIDATOR`, `RESOLVER`, `LINKER`,
`ONTOLOGIST`, `SUMMARIZER`, `REPORTER`, `STRATEGIST`, `EXAMINER`, `CREW`, `SEARCHER`. For example
`LLM_MODEL_EXAMINER=anthropic:claude-opus-5-5` grades the exam on Claude while everything else runs locally.
