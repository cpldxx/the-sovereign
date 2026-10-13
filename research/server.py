"""Research service — DeerFlow missions whose pages become knowledge-graph episodes.

    uv run python server.py   → http://localhost:8070

Needs the KG API (8080), Ollama, and the search/crawler containers (`docker compose up -d`).
Agents reach it through the KG: the Head assigns the researcher a task (assign_task research.investigate),
research_status / list_research show the jobs.
"""

import asyncio
import json
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

# Each domain learns on its own schedule (Settings › Learning: times in the user's timezone, kept by the KG —
# kg/core/learning.py). RESEARCH_SCHEDULE=off stops every cycle; runs can still be started by hand.
SCHEDULE = os.getenv("RESEARCH_SCHEDULE", os.getenv("RESEARCH_NIGHTLY", "on")).lower() in ("1", "on", "true", "yes")
REVIEW_MINUTES = int(os.getenv("NIGHTLY_REVIEW_MINUTES", "45"))   # the Head's review budget per domain and cycle
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")
REPORT_WAIT = 18 * 3600  # seconds a cycle waits for its research runs before the rest of it goes ahead
SLOTS_FILE = Path(os.getenv("RESEARCH_DATA") or HERE / ".data") / "cycles.json"

jobs = Jobs()
_cycling: set[str] = set()


def _slots() -> dict[str, list[str]]:
    try:
        return json.loads(SLOTS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _ran(domain: str, slot: str) -> None:
    done = _slots()
    done[domain] = (done.get(domain, []) + [slot])[-30:]   # a restart doesn't run a slot twice
    SLOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SLOTS_FILE.write_text(json.dumps(done))


async def scheduler() -> None:
    """Every minute: start a domain's learning cycle when one of its times comes."""
    checked_on: str | None = None
    while True:
        await asyncio.sleep(60)
        try:
            async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(), timeout=60) as kg:
                domains = [d["id"] for d in (await kg.get("/domains")).json()["domains"]]
                due = {}
                for d in domains:
                    r = await kg.get(f"/domains/{d}/learning")
                    slot = r.json().get("due") if r.is_success else None
                    if slot and slot not in _slots().get(d, []) and d not in _cycling:
                        due[d] = slot
                if due and checked_on != datetime.now().date().isoformat():
                    # The safeguards first, once a day: a failure becomes a critical alert in every domain.
                    checked_on = datetime.now().date().isoformat()
                    check = (await kg.post("/system/selfcheck")).json()
                    print(f"[Research] security self-check {'ok' if check.get('ok') else 'FAILED'}: "
                          f"{ {k: v['status'] for k, v in check.get('checks', {}).items()} }", flush=True)
        except httpx.HTTPError as e:
            print(f"[Research] schedule: KG unreachable ({type(e).__name__})", flush=True)
            continue
        for d, slot in due.items():
            _ran(d, slot)
            start_cycle(d, slot)


def start_cycle(domain: str, slot: str) -> None:
    _cycling.add(domain)
    task = asyncio.get_running_loop().create_task(cycle(domain, slot))
    task.add_done_callback(lambda _t: _cycling.discard(domain))


async def cycle(d: str, slot: str) -> None:
    """One learning cycle (kg/core/learning.py): learn what's new and what the graph is unsure of, then keep what
    holds, forget what never did, and brief."""
    started, summary = time.monotonic(), {"slot": slot, "started_at": jobs_now(), "questions": [], "runs": []}
    print(f"[Research] {d}: learning cycle {slot}", flush=True)
    async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(), timeout=httpx.Timeout(3600, connect=10)) as kg:
        async def step(label: str, method: str, path: str, show=lambda b: b, **kw):
            try:
                r = await kg.request(method, path, **kw)
                body = r.json() if r.is_success else None
                print(f"[Research] {d}: {label}: {show(body) if body is not None else f'failed ({r.status_code})'}",
                      flush=True)
                return body
            except (httpx.HTTPError, ValueError) as e:
                print(f"[Research] {d}: {label} failed ({type(e).__name__})", flush=True)
                return None

        # Learn: what's new (a first bootstrap for a new domain), then the questions for the graph's gaps.
        if not jobs.active(d):
            mode = "update" if jobs.last(d, "bootstrap") else "bootstrap"
            summary["runs"].append(jobs.submit(d, mode)["id"])
            if mode == "update":
                asked = await step("questions", "POST", f"/domains/{d}/learning/questions",
                                   lambda b: [q["question"] for q in b.get("questions", [])])
                for q in (asked or {}).get("questions", []):
                    summary["questions"].append(q)
                    summary["runs"].append(jobs.submit(d, "mission", q["question"])["id"])
        while jobs.active(d) and time.monotonic() - started < REPORT_WAIT:
            await asyncio.sleep(60)
        # Keep and forget: live sources checked, unconfirmed weak facts retired, the Head decides the review
        # queue — so the report shows what the graph now holds — then the briefing and the playbooks.
        await step("sensors", "POST", f"/domains/{d}/sensors/check", lambda b: b.get("sensors"))
        forgot = await step("forget", "POST", f"/domains/{d}/learning/forget", json={"dry_run": False})
        await step("Head reviews", "POST", f"/domains/{d}/head-review", params={"minutes": REVIEW_MINUTES})
        await step("report", "POST", f"/domains/{d}/reports", lambda b: b.get("headline", ""),
                   json={"hours": 24, "wait": True})
        await step("playbooks", "POST", f"/domains/{d}/playbooks/cycle", json={"hours": 24, "wait": True})
        ran = [jobs.get(j) for j in summary["runs"]]
        summary.update(
            finished_at=jobs_now(),
            runs=[{"id": j["id"], "mode": j["mode"], "question": j.get("question", ""), "status": j["status"],
                   "error": (j.get("error") or "")[:200], "summary": j.get("summary") or {}} for j in ran if j],
            forgotten={k: (forgot or {}).get(k, 0) for k in ("facts", "entities")})
        await step("record", "PUT", f"/domains/{d}/learning/last", lambda b: "ok", json=summary)


def jobs_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(jobs.worker())]
    if SCHEDULE:
        tasks.append(asyncio.create_task(scheduler()))
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


@app.post("/domains/{domain}/cycle")
async def run_cycle(domain: str):
    """Run a learning cycle now: what's new, the graph's open questions, then keep, forget and brief."""
    if domain in _cycling:
        raise HTTPException(status_code=409, detail="A learning cycle is already running for this domain")
    async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(domain), timeout=10) as kg:
        if (await kg.get(f"/domains/{domain}")).status_code == 404:
            raise HTTPException(status_code=404, detail=f"Domain '{domain}' does not exist")
    slot = f"manual {jobs_now()[:16]}"
    start_cycle(domain, slot)
    return {"started": slot}


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
    finished = [j for j in jobs.list(None, 50) if j["status"] in ("done", "failed")]
    last = finished[0] if finished else None
    return {
        "research": True,
        # Broken setup or a failing last run: the sidebar turns red instead of three nights of silent failures
        # (2026-10-10 → 12: every run failed on DeerFlow's config lookup while everything looked green).
        "config": runner.config_ok(),
        "last_failed": (last["error"] or "failed")[:300] if last and last["status"] == "failed" else None,
        "model": runner.model_name(),
        "search": runner.search_provider(),
        # An API search provider doesn't need the local SearXNG.
        # SearXNG's own health check: a real search waits on its slowest engine (DuckDuckGo timing out made it
        # look down while results came back).
        "searxng": runner.search_provider() != "searxng" or await up(f"{SEARXNG_URL}/healthz"),
        "crawler": await up(f"{fetch.CRAWL4AI_URL}/health"),
        "schedule": "per domain (Settings › Learning)" if SCHEDULE else "off",
        "tracing": runner.tracing(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8070)
