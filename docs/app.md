# The web app

One screen per domain: the agents at work on the left, and three tabs on the right — you talk to the Head, read what
happened, and set things up; the rest is the Head's work. Drawn in graphite (one
neutral scale, a bone-white accent, Geist type bundled with the app): state shows as brightness and motion, not color.

Left — what the team is doing:
- **Agents** (the main view) — the Head, the knowledge graph and the team as one picture: the Head on top, the
  graph at the centre with its counts, the Researcher and the learning pipeline's stations on a dial around it. A
  teammate or station at work turns white, its ring turns and its line to the graph flows, with what it is doing beside it; every hand-off travels
  its line as a comet whose shape tells the kind (● task · ○ report · ■ tool call · ◆ facts stored). The console below
  lists the hand-offs with live counts of agents working, tasks queued and hand-offs per minute
  (`GET /domains/{domain}/activity`, polled). **Click a teammate** (any station opens Learning) for who it is, what it is doing and its conversations
  with the Head (tasks, questions, answers, reports, the Head's decisions); the Researcher also shows its runs —
  bootstrap, "what's new" and missions, each with the pages read, what each added and the report; the Head shows
  every conversation.
- **Knowledge graph** (a tab of the main view, drawn only while open) — the 300 most connected entities by default
  (1000 / all to choose; `GET /graph/{domain}?limit=&include=`), plus whatever the Head just talked about. It is
  an Obsidian-style live graph: entities tinted by type and sized by their links, faint links that brighten as facts gain evidence, names fading in as you zoom, hover to light an entity and its neighbours, drag to shake it; entities no fact connects yet float as a cloud around it; toggle superseded facts. Click an entity for its facts, history and sources. **Search the graph** opens beside it (Graph RAG: closest entities and facts by meaning plus the strongest facts around them, with sources) and lights up what it finds. Refreshes by itself when agents add knowledge.

Right — three tabs:
- **Head** — streaming chat: each KG tool call shows live, what the Head says on the way stays above its answer,
  and the nodes it read or stored light up in the graph. The conversation is kept in the domain's database, per
  person (Hermes saves each question when it arrives and each answer when it is done — a reload mid-answer loses
  nothing; the answer appears when it lands; `GET /domains/{d}/chat`). 📎 adds a source: paste an article or notes with its source, the pipeline learns
  it (new / known entities, created / strengthened / superseded / rejected facts with reasons) and the chat notes it.
- **Voice** (in the Head tab) — press the mic and just talk: local speech recognition (Whisper on the Apple GPU, ~1 s) and local voices (macOS, English and Korean) — nothing leaves this machine. Pauses end your turn; the Head answers in a few spoken sentences (the full answer stays on screen), says "let me check" while it works, and stops when you talk over it. Once a day it opens with the daily briefing. Optional wake word ("Sovereign, …" / "자비스, …").
- **Briefing** — the briefing each learning cycle writes: what the graph learned, what changed (superseded and
  confirmed facts), what needs attention; read it aloud, highlight its entities in the graph, or write one now.
- **Settings** — *Learning*: when the graph learns by itself, how many questions it pursues, how long an unconfirmed
  weak fact may wait (below). *Ontology*: the domain's grammar with per-type counts; edit it or have the
  Ontologist regenerate it. *Review*: the ambiguous writes (weak facts, possible merges and links) — the Head
  decides them by itself every 30 min (`HEAD_REVIEW_EVERY_MINUTES`, a sweep of at most `HEAD_REVIEW_MINUTES`,
  default 10) and every night before the report (at most `NIGHTLY_REVIEW_MINUTES`, default 45 per domain), with its
  notes on a thread; "Head review" makes it decide now, and you can still overrule one.
- **Status** — KG API / ArcadeDB / Hermes / Research / search + crawler health in the sidebar, plus a link to the LangFuse traces when tracing is on (admins).
- **Accounts** — sign in; Members (header) shares a domain with other accounts as viewer / editor / owner; Account (sidebar) changes the password, makes API tokens for MCP clients and, for admins, invite links.

Backend URLs default to localhost; override with `VITE_KG_URL` / `VITE_HERMES_URL` (see `frontend/.env.example`).
