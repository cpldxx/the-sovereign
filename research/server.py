"""Research service — DeerFlow missions whose pages become knowledge-graph episodes.

    uv run python server.py   → http://localhost:8070

Needs the KG API (8080), Ollama, and the search/crawler containers (`docker compose up -d`).
Agents reach it through the KG's MCP tools (start_research / research_status / list_research).
"""

import asyncio
import os
import time
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

from sovereign_research import auth, fetch, runner, search  # noqa: E402
from sovereign_research.jobs import KG_URL, Jobs  # noqa: E402

NIGHTLY = os.getenv("RESEARCH_NIGHTLY", "on").lower() in ("1", "on", "true", "yes")
NIGHTLY_HOUR = int(os.getenv("RESEARCH_NIGHTLY_HOUR", "3"))  # local time
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")
REPORT_WAIT = 18 * 3600  # seconds the nightly report waits for a domain's research runs

jobs = Jobs()


async def nightly() -> None:
    """Once a day at NIGHTLY_HOUR: a 'what's new' run per domain (a bootstrap for never-researched ones),
    then — once a domain's runs are finished — its daily report (the KG refreshes stale summaries first)."""
    ran_on: str | None = None
    while True:
        await asyncio.sleep(600)
        today = datetime.now().date().isoformat()
        if datetime.now().hour != NIGHTLY_HOUR or ran_on == today:
            continue
        ran_on = today
        try:
            async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(), timeout=30) as kg:
                domains = [d["id"] for d in (await kg.get("/domains")).json()["domains"]]
        except httpx.HTTPError as e:
            print(f"[Research] nightly: KG unreachable ({type(e).__name__})", flush=True)
            continue
        for d in domains:
            if jobs.active(d):
                continue
            mode = "update" if jobs.last(d, "bootstrap") else "bootstrap"
            jobs.submit(d, mode)
            print(f"[Research] nightly: queued {mode} for {d}", flush=True)
        await daily_reports(domains)


async def daily_reports(domains: list[str]) -> None:
    """Each domain's sensor check, daily report and playbook cycle, as soon as its research runs are finished (runs are
    sequential). A run still going after REPORT_WAIT doesn't hold them back — nor the next night, which waits
    on this loop."""
    pending = list(domains)
    deadline = time.monotonic() + REPORT_WAIT
    while pending:
        idle = [d for d in pending if not jobs.active(d) or time.monotonic() > deadline]
        for d in idle:
            pending.remove(d)
            try:
                # Summary refresh + report is LLM work on the KG side: minutes with a local model.
                async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(), timeout=httpx.Timeout(3600, connect=10)) as kg:
                    # Every sensor source read once: broken ones sink, thin sensors get scouted again.
                    r = await kg.post(f"/domains/{d}/sensors/check")
                    print(f"[Research] nightly: sensors for {d}: "
                          f"{r.json().get('sensors') if r.is_success else f'failed ({r.status_code})'}", flush=True)
                    r = await kg.post(f"/domains/{d}/reports", json={"hours": 24, "wait": True})
                    print(f"[Research] nightly: report for {d}: "
                          f"{r.json().get('headline', '') if r.is_success else f'failed ({r.status_code})'}", flush=True)
                    # Then the playbooks: today's changes checked against them (triggered ones become
                    # proposals), and the set rewritten from the graph.
                    r = await kg.post(f"/domains/{d}/playbooks/cycle", json={"hours": 24, "wait": True})
                    print(f"[Research] nightly: playbooks for {d}: "
                          f"{r.json() if r.is_success else f'failed ({r.status_code})'}", flush=True)
            except httpx.HTTPError as e:
                print(f"[Research] nightly: report / playbooks for {d} failed ({type(e).__name__})", flush=True)
        if pending:
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(jobs.worker())]
    if NIGHTLY:
        tasks.append(asyncio.create_task(nightly()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Sovereign Research", description="DeerFlow research → KG episodes", lifespan=lifespan)
# Added before CORS, so CORS stays outermost and a 401 still carries CORS headers.
app.add_middleware(auth.Auth)
app.add_middleware(CORSMiddleware, allow_origins=auth.ORIGINS, allow_credentials=True, allow_methods=["*"],
                   allow_headers=["*"])


class ResearchRequest(BaseModel):
    mode: Literal["bootstrap", "update", "mission"] = "update"
    question: str = ""


@app.post("/domains/{domain}/research")
async def research(domain: str, request: ResearchRequest):
    """Queue a research job. bootstrap: broad first research + ontology from real sources;
    update: what's new since the last runs; mission: answer `question`."""
    if request.mode == "mission" and not request.question.strip():
        raise HTTPException(status_code=422, detail="A mission needs a question")
    async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(domain), timeout=10) as kg:
        try:
            r = await kg.get(f"/domains/{domain}")
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail=f"KG API unreachable at {KG_URL}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Domain '{domain}' does not exist")
    if request.mode == "bootstrap" and (active := jobs.active(domain)) and active["mode"] == "bootstrap":
        return active
    return jobs.submit(domain, request.mode, request.question.strip())


class FetchRequest(BaseModel):
    url: str
    max_chars: int = 12000


@app.post("/fetch")
async def fetch_now(request: FetchRequest):
    """Read one page now — a live sense for agents. Same rules as research runs: public URLs only,
    robots.txt respected, per-site pacing, rendered by the crawler."""
    page = await fetch.fetch_page(request.url)
    return {"url": page.url, "title": page.title, "status": page.status, "error": page.error,
            "chars": len(page.markdown), "markdown": page.markdown[: max(0, min(request.max_chars, 50_000))]}


@app.get("/robots")
async def robots(url: str):
    """Whether robots.txt lets Sovereign read this URL (and it is a public address) — sensors check API endpoints
    with it; pages are checked by /fetch itself."""
    if await asyncio.to_thread(fetch.validate_public_http_url, url, allow_private_addresses=False):
        return {"allowed": False, "reason": "not a public http(s) address"}
    allowed = await fetch._robots_allows(url)
    return {"allowed": allowed, "reason": "" if allowed else "disallowed by robots.txt"}


@app.get("/search")
async def search_now(q: str, time_range: Literal["day", "week", "month", "year"] | None = None,
                     limit: int = Query(8, ge=1, le=20)):
    """Web search now (local SearXNG, paced) — a live sense for agents; `time_range` for recent results.
    Several free engines ignore time ranges and then return nothing: such a search is retried without it."""
    results, unresponsive = await search.search(q, time_range)
    filtered = bool(time_range)
    if not results and time_range:
        results, unresponsive = await search.search(q)
        filtered = False
    return {"results": results[:limit], "time_range_applied": filtered, "unresponsive": unresponsive}


@app.get("/jobs")
async def list_jobs(domain: str | None = None, limit: int = Query(50, ge=1, le=500)):
    """Research jobs — of one domain, or of every domain the caller can see."""
    if domain:
        auth.require(domain)
        return {"jobs": jobs.list(domain, limit), "queued": jobs.queue.qsize()}
    principal = auth.current.get()
    return {"jobs": [j for j in jobs.list(None, 10_000) if principal.can(j["domain"])][:limit],
            "queued": jobs.queue.qsize()}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job or not auth.current.get().can(job["domain"]):
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
        "tracing": runner.tracing(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8070)
