# Prototype (reference only)

In-process Head Agent from before the split. These files imported the KG code
directly (`agents.*`, `core.database`) and no longer run from here.

Kept as reference for rebuilding the Head Agent on a separate Hermes process
that talks to the Sovereign KG API (`../../kg`).

- `head_agent.py` — Hermes `AIAgent` setup + KG-grounded system prompt
- `kg_tools.py`   — `query_knowledge_graph` / `ingest_data` tool registration
- `agent.py`      — old `POST /domains/{domain}/ask` FastAPI route
