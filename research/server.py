"""Research service — DeerFlow missions whose pages become knowledge-graph episodes.

    uv run python server.py   → http://localhost:8070

Needs the KG API (8080), Ollama, and the search/crawler containers (`docker compose up -d`).
Agents reach it through the KG's MCP tools (start_research / research_status / list_research).
"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
# DeerFlow resolves its home at import time; keep it inside this project.
os.environ["DEER_FLOW_HOME"] = str((HERE / os.getenv("DEER_FLOW_HOME", ".deerflow-home")).resolve())

import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from sovereign_research import fetch, runner  # noqa: E402
from sovereign_research.jobs import KG_URL, Jobs  # noqa: E402

NIGHTLY = os.getenv("RESEARCH_NIGHTLY", "on").lower() in ("1", "on", "true", "yes")
NIGHTLY_HOUR = int(os.getenv("RESEARCH_NIGHTLY_HOUR", "3"))  # local time
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")

jobs = Jobs()


async def nightly() -> None:
    """Once a day at NIGHTLY_HOUR: a 'what's new' run per domain (a bootstrap for never-researched ones)."""
    ran_on: str | None = None
    while True:
        await asyncio.sleep(600)
        today = datetime.now().date().isoformat()
        if datetime.now().hour != NIGHTLY_HOUR or ran_on == today:
            continue
        ran_on = today
        try:
            async with httpx.AsyncClient(base_url=KG_URL, timeout=30) as kg:
                domains = (await kg.get("/domains")).json()["domains"]
        except httpx.HTTPError as e:
            print(f"[Research] nightly: KG unreachable ({type(e).__name__})", flush=True)
            continue
        for d in domains:
            if jobs.active(d["id"]):
                continue
            mode = "update" if jobs.last(d["id"], "bootstrap") else "bootstrap"
            jobs.submit(d["id"], mode)
            print(f"[Research] nightly: queued {mode} for {d['id']}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(jobs.worker())]
    if NIGHTLY:
        tasks.append(asyncio.create_task(nightly()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Sovereign Research", description="DeerFlow research → KG episodes", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


class ResearchRequest(BaseModel):
    mode: Literal["bootstrap", "update", "mission"] = "update"
    question: str = ""


@app.post("/domains/{domain}/research")
async def research(domain: str, request: ResearchRequest):
    """Queue a research job. bootstrap: broad first research + ontology from real sources;
    update: what's new since the last runs; mission: answer `question`."""
    if request.mode == "mission" and not request.question.strip():
        raise HTTPException(status_code=422, detail="A mission needs a question")
    async with httpx.AsyncClient(base_url=KG_URL, timeout=10) as kg:
        try:
            r = await kg.get(f"/domains/{domain}")
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail=f"KG API unreachable at {KG_URL}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' does not exist")
    if request.mode == "bootstrap" and (active := jobs.active(domain)) and active["mode"] == "bootstrap":
        return active
    return jobs.submit(domain, request.mode, request.question.strip())


@app.get("/jobs")
async def list_jobs(domain: str | None = None, limit: int = Query(50, ge=1, le=500)):
    return {"jobs": jobs.list(domain, limit), "queued": jobs.queue.qsize()}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    if not (job := jobs.get(job_id)):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@app.get("/health")
async def health():
    async def up(url: str, **kw) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(url, **kw)).status_code < 500
        except httpx.HTTPError:
            return False
    return {
        "research": True,
        "model": runner.model_name(),
        "search": runner.search_provider(),
        # An API search provider doesn't need the local SearXNG.
        "searxng": runner.search_provider() != "searxng"
        or await up(f"{SEARXNG_URL}/search", params={"q": "ping", "format": "json"}),
        "crawler": await up(f"{fetch.CRAWL4AI_URL}/health"),
        "nightly": f"{NIGHTLY_HOUR:02d}:00" if NIGHTLY else "off",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8070)
