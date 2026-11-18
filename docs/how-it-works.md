# How it works

Each topic is a **domain**: its own knowledge graph and a team of agents that researches it on a schedule
(03:00 every night by default; Settings › Learning). One learning cycle:

1. **Charter** — when you create a domain, the Head (the lab's lead agent) asks what you actually want to know and
   writes a research charter with you: the goal, the core questions, what to leave out, how deep. Every research run
   steers by it.
2. **Research** — the researcher searches the web and the scholarly databases (OpenAlex, arXiv, PubMed), reads what
   it finds, and keeps the full pages. Each source is weighed by kind: papers, then official sources, then news,
   then blogs.
3. **Learning** — every page becomes facts in the graph, each with its source. A fact more sources confirm grows
   stronger; one with a single source stays a weak signal; a newer report supersedes an older one; what never holds
   up is forgotten.
4. **Exam** — the lab answers a fixed set of questions, written from authoritative sources the research is never
   allowed to read, from its graph alone. The score is kept per cycle. The questions it can't answer become the next
   cycle's research.
5. **Answers** — ask the Head anything about the topic: it answers from the graph, with its sources and how sure it
   is. Each cycle also ends with a briefing: what the graph learned, what changed, what needs attention.

The architecture, the ingest pipeline and the API are in the [README](../README.md#architecture).

## What the lab keeps to

- **It never goes around a block.** No getting past robots.txt, logins, paywalls or bot checks; it identifies itself
  honestly and paces each site. A page it couldn't read is recorded as unread.
- **It weighs its sources.** Papers, then official sources, then news, then blogs. A claim with one source stays a
  weak signal until others confirm it.
- **It doesn't act in the world.** No messages, orders or bookings. It finds out what is true and explains it.
- **It stays on your machine.** The graph and its records live on the server you run. With local models your
  questions and the pages it reads stay there too ([models](models.md)).

## One real night

The record of one learning cycle in a test domain on chip packaging (11 November 2026):

| Step | Pages read | Facts stored |
|---|---:|---:|
| Mission: standards for copper surface prep before hybrid bonding | 16 | 532 |
| Mission: AI accelerators and HBM stacks that use Cu-Cu hybrid bonding | 7 | 216 |
| Mission: process bottlenecks of chiplet integration, and their fixes | 7 | 99 |
| Mission: what is settled in the HBM4 spec, and why fine-pitch bumps | 8 | 54 |
| What changed this week | 9 | 109 |
| **In all** | **47** | **1,010** |

It forgot 1 fact that never held up, then sat its exam: **35 of 100** — of 30 questions, 7 answered, 7 answered in
part, 16 open (an answered question counts 2 points, a partial one 1). The same exam over its last six sittings:

| 2 Nov | 3 Nov | 4 Nov | 5 Nov | 6 Nov | 11 Nov |
|---:|---:|---:|---:|---:|---:|
| 32 | 32 | 28 | 33 | 32 | 35 |

The score doesn't rise every night; it is always measured the same way, so it shows what the graph knows rather than
what it was told.

## What's inside

| Part | What it does |
|---|---|
| `kg/` | The knowledge graph API: ingest, graph search, ontology, exam, learning schedule (FastAPI, MCP, Pydantic AI) |
| `research/` | The researcher: searches, reads, finds papers, and sends the pages to the graph (DeerFlow, SearXNG, crawl4ai) |
| `hermes/` | The Head: talks with you, leads the team, answers by voice too (Hermes Agent) |
| ArcadeDB | One database per domain, graph and vectors together |
| `frontend/` | The team at work, the graph, the Head's chat, briefings, settings (React, Vite) |
| `sovereign` | One command to set it up, run it, test it and check its security |
