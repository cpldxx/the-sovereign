"""Stage timings (always) and LangFuse tracing (optional) for the KG pipeline.

Tracing is on when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set; LANGFUSE_BASE_URL points at
the server (self-hosted: `docker compose --profile observability up -d` → http://localhost:3000).
Every pydantic-ai agent run is then traced with its prompt, output, tokens and latency, nested under
the pipeline stage that made it. Stage timings are measured either way and returned with each ingest.
"""

import json
import os
import time
from contextlib import contextmanager

import httpx
from dotenv import load_dotenv

load_dotenv()

# Keys set and not switched off (LANGFUSE_TRACING=false: Docker without the observability profile, where no
# LangFuse runs to receive the traces).
ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")) and \
    os.getenv("LANGFUSE_TRACING", "true").strip().lower() not in ("false", "off", "0", "no")
_client = None


def setup() -> bool:
    """Start exporting traces if LangFuse is configured. Call once at startup."""
    global _client
    if not ENABLED or _client is not None:
        return _client is not None
    from langfuse import get_client
    from pydantic_ai import Agent

    _client = get_client()
    Agent.instrument_all()
    return True


def shutdown() -> None:
    if _client is not None:
        _client.flush()


@contextmanager
def observe(name: str, *, as_type: str = "span", input=None, metadata=None, tags: list[str] | None = None):
    """A LangFuse observation around a block, nested under the current one. Yields it (None when off).
    `tags` (top-level observations only) label the whole trace, e.g. with the domain."""
    if _client is None:
        yield None
        return
    from langfuse import propagate_attributes

    with _client.start_as_current_observation(name=name, as_type=as_type, input=input, metadata=metadata) as obs:
        if tags:
            with propagate_attributes(tags=tags, trace_name=name):
                yield obs
        else:
            yield obs


class Timings(dict):
    """Seconds per stage, summed over repeated stages (e.g. one per chunk)."""

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        with observe(name):
            try:
                yield
            finally:
                self[name] = round(self.get(name, 0.0) + time.perf_counter() - start, 2)


# Trace names → what the owner calls them.
_ACTIVITY = {"Hermes turn": "head agent", "lead-agent": "research", "daily_report": "daily report"}


async def llm_usage(start: str, end: str) -> list[dict] | None:
    """LLM calls, tokens and model time per activity (trace name) over a period, from LangFuse.
    None when tracing is off or LangFuse is unreachable."""
    if not ENABLED:
        return None
    query = {
        "view": "observations",
        "metrics": [{"measure": "count", "aggregation": "count"},
                    {"measure": "totalTokens", "aggregation": "sum"},
                    {"measure": "latency", "aggregation": "sum"}],
        "dimensions": [{"field": "traceName"}],
        "filters": [{"column": "type", "operator": "=", "value": "GENERATION", "type": "string"}],
        "fromTimestamp": start, "toTimestamp": end,
    }
    try:
        async with httpx.AsyncClient(
            base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
            auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]), timeout=15,
        ) as client:
            r = await client.get("/api/public/v2/metrics", params={"query": json.dumps(query)})
            r.raise_for_status()
            rows = r.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    usage = [{"activity": _ACTIVITY.get(row["traceName"], row["traceName"] or "other"),
              "calls": row["count_count"], "tokens": int(row["sum_totalTokens"] or 0),
              "seconds": round((row["sum_latency"] or 0) / 1000)} for row in rows]
    return sorted(usage, key=lambda u: -u["tokens"])
