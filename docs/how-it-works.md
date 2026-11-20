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

The parts and the ingest pipeline are in [architecture](architecture.md), the routes in [API](api.md).

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

## In more depth

```
first       you give a topic → the Head interviews you → a research charter (goal, core questions, in scope, leave
            out, depth) → you agree → the first deep research: a broad first study + one mission per core question
on demand   you ask → the Head checks the graph → what it lacks goes to the Researcher (a mission) → every page read
            is learned through the pipeline → the Head is woken with the report and answers
by itself   a learning cycle at the times you set (Settings › Learning): what's new + the Strategist's questions for
            the graph's gaps → keep what holds, forget what never did → the Head decides the review queue → the exam
            → the synthesis → briefing
```

- **Growth you can measure.** The domain sits an exam after every cycle. Its questions are written from
  authoritative sources, never from a model's memory: per core question of the charter, web searches for papers,
  reviews and official documents, the best result read in full, and from it a basic, a working and a deep question,
  each with its reference answer. Round trip: the Validator checks each question-and-answer against the page, and one
  the page doesn't state is dropped — so no question rests on a detail a model made up. The exam's sources are
  **held out** (research skips them, like a test set never trained on): the graph has to learn the answers elsewhere.
  At each sitting the examiner writes the graph's answer from the facts Graph RAG returns — without seeing the reference,
  so it can't leak in — then grades that answer against the reference apart (2 / 1 / 0) and flags a contradiction. Briefing › Growth shows the score over time, each core question's state and
  every question with what its source says and what the graph says; the open ones are the next cycle's first research.
  Off-the-mark questions can be dropped, or new ones set; `LLM_MODEL_EXAMINER=anthropic:<model>` lets a stronger model
  write and grade it. (Methods: roundtrip-consistent question generation, Alberti et al. 2019; premise verification
  against retrieved evidence, EMNLP 2025; claim-level checking as in DeepMind's SAFE; answering in isolation as in
  Chain-of-Verification's factored variant.)
- **Papers first, and every source weighed by what it is.** The Researcher searches the scholarly literature —
  OpenAlex (every field), arXiv and PubMed, official APIs with abstracts and open-access full text — with
  `paper_search` / `read_paper`, beside `web_search` / `web_fetch`. Every source gets a tier from its URL
  (`kg/core/sources.py`): paper · official · reference · press · web · blog. The tier caps how far a fact it states is
  trusted on its own (paper 1.0, official 0.95, press 0.85, web 0.75, blog 0.6 — a blog alone barely enters the graph);
  confirmation by other sources still adds up. The Head says what kind of source a fact rests on, and every source list
  shows it.
- **Two layers.** Beneath, the facts with their sources — the truth. Above, a living review of each core question
  (`kg/core/synthesis.py`, Briefing › Knowledge): the current answer, what confirmed sources agree on, what is debated
  or changed (superseded facts), what is still open, and the key sources — every point citing the facts it rests on (a
  point citing none is dropped), the sources counted, papers and official ones first. Rewritten after each cycle where
  its facts changed; the Head starts broad questions from it (`read_synthesis`).
- **The charter steers everything.** Research missions, the Strategist's questions, the ontology and the Head read
  it; the Head suggests an addition when you keep asking about something it lacks, and you take it or leave it
  (Settings › Charter). It stays yours to edit — or start over with a new interview.

- **A research engine, not an agent that acts.** Nothing is sent, ordered or booked; the Head finds out what is true,
  keeps it as checked facts with their sources, and says how sure it is.
- **What is true now** (a price, today's news) the Head reads live with `web_search` / `read_webpage` — a real browser
  through the research service — and stores only what is worth keeping, through the same pipeline.
- **robots.txt is respected** for every page the research service opens (each redirect too), and it reaches public
  addresses only. Bot protection, logins and paywalls are never worked around.
- **Roles are endpoints.** `/mcp` serves the Head everything; `/mcp/readonly` serves reading tools only (e.g. to point
  another assistant at your graph safely).

**Learning cycles.** each domain grows by itself on the schedule you set in Settings › Learning (times in your
timezone, several a day if you like; default 03:00 in the Mac's timezone). A cycle: a "what's new" run (a bootstrap
for a new domain), plus research questions the Strategist picks from what the graph is unsure or silent about —
facts resting on one weak source, well-connected entities nothing new was heard about for a month, questions you
asked that the Head had to take to the web, words the ontology lacked (`curiosity`, 0–5 a cycle). Once its runs are
done: **forgetting** (a fact from one weak source nobody confirmed in `forget_after_days`, default 30, is retired —
kept as history, never used as an answer; an entity left with nothing else goes; nothing a pending review relies
on), Head review and the briefing, each step on its own (one that fails doesn't skip the rest). Settings › Learning shows the last cycle (questions, facts learned / confirmed / forgotten), what the
graph is unsure about now, and runs one on demand (`POST research /domains/{d}/cycle`). A run that can't reach the KG before its research is done (a timeout — e.g. the Mac swapping) is tried once more 10 min later (`RESEARCH_RETRY_MINUTES`).

Search uses the local SearXNG by default. Its free engines throttle heavy use (searches are paced, and Sovereign never works around a block); for reliable daily research set `TAVILY_API_KEY` or `BRAVE_API_KEY` in `research/.env` (both have free tiers).
