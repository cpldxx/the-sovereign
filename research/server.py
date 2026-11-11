"""Research service — DeerFlow missions whose pages become knowledge-graph episodes.

    uv run python server.py   → http://localhost:8070

Needs the KG API (8080), Ollama, and the search/crawler containers (`docker compose up -d`).
Agents reach it through the KG: the Head assigns the researcher a task (assign_task research.investigate),
research_status / list_research show the jobs.
"""

import asyncio
import json
import math
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
from pydantic import BaseModel, Field  # noqa: E402

from sovereign_research import auth, fetch, papers, runner, search  # noqa: E402
from sovereign_research.jobs import KG_URL, Jobs, others_loaded  # noqa: E402

# Each domain learns on its own schedule (Settings › Learning: times in the user's timezone, kept by the KG —
# kg/core/learning.py). RESEARCH_SCHEDULE=off stops every cycle; runs can still be started by hand.
SCHEDULE = os.getenv("RESEARCH_SCHEDULE", os.getenv("RESEARCH_NIGHTLY", "on")).lower() in ("1", "on", "true", "yes")
REVIEW_MINUTES = int(os.getenv("NIGHTLY_REVIEW_MINUTES", "45"))   # the Head's review budget per domain and cycle
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8088").rstrip("/")
REPORT_WAIT = 18 * 3600  # seconds a cycle waits for its research runs before the rest of it goes ahead
SLOTS_FILE = Path(os.getenv("RESEARCH_DATA") or HERE / ".data") / "cycles.json"
# A cycle whose time comes while other apps' models fill the memory (jobs.others_loaded) waits for them, this long
# at most — past it, that day's cycle is let go. (In memory: a restart meanwhile lets it go too.)
MEMORY_WAIT_HOURS = float(os.getenv("RESEARCH_MEMORY_WAIT_HOURS", "8"))
SELFCHECK_FILE = SLOTS_FILE.with_name("selfcheck.txt")   # the local day the security self-check last ran

jobs = Jobs()
_cycling: set[str] = set()
_waiting: dict[str, tuple[str, float]] = {}   # domain → (slot, when it came) of cycles waiting for memory


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


def _checked_on() -> str:
    try:
        return SELFCHECK_FILE.read_text().strip()
    except OSError:
        return ""


async def _selfcheck(kg: httpx.AsyncClient) -> None:
    """The safeguards, once a local day — whatever the domains' schedules (it used to run only when some domain's
    learning time came): a failure is left in the Head's inbox in every domain."""
    today = datetime.now().date().isoformat()
    if _checked_on() == today:
        return
    try:
        r = await kg.post("/system/selfcheck", timeout=600)
    except httpx.ConnectError:
        return                      # the KG is down: try again next minute
    except httpx.TimeoutException:
        r = None                    # it ran on the KG side all the same
    SLOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELFCHECK_FILE.write_text(today)   # one try a day
    if r is None:
        print("[Research] security self-check: no answer in 10 min", flush=True)
        return
    check = r.json() if r.is_success else {}
    print(f"[Research] security self-check {'ok' if check.get('ok') else f'FAILED (HTTP {r.status_code})'}: "
          f"{ {k: v.get('status') for k, v in check.get('checks', {}).items()} }", flush=True)


async def scheduler() -> None:
    """Every minute: start a domain's learning cycle when one of its times comes."""
    while True:
        await asyncio.sleep(60)
        # Anything may go wrong in a tick (a 401 without "domains", a plain-text 500): it is logged and the next
        # tick tries again. Until 2026-10-14 only httpx errors were caught, and one KeyError ended the scheduler —
        # no domain learned again until a restart.
        try:
            due = {}
            async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(), timeout=60) as kg:
                try:
                    await _selfcheck(kg)
                except Exception as e:
                    print(f"[Research] security self-check failed to run ({type(e).__name__}: {e})", flush=True)
                r = await kg.get("/domains")
                if not r.is_success:
                    print(f"[Research] schedule: KG answered {r.status_code} for /domains", flush=True)
                    continue
                for d in [d["id"] for d in r.json()["domains"]]:
                    r = await kg.get(f"/domains/{d}/learning")
                    slot = r.json().get("due") if r.is_success else None
                    if slot and slot not in _slots().get(d, []) and d not in _cycling and d not in _waiting:
                        due[d] = slot
            for d, slot in due.items():
                _waiting[d] = (slot, time.monotonic())
            if _waiting and (busy := await others_loaded()):
                for d, (slot, since) in list(_waiting.items()):
                    if d in due:
                        print(f"[Research] {d}: learning cycle {slot} waits — other models fill the memory "
                              f"({', '.join(busy)})", flush=True)
                    elif time.monotonic() - since > MEMORY_WAIT_HOURS * 3600:
                        _ran(d, slot)
                        del _waiting[d]
                        print(f"[Research] {d}: learning cycle {slot} let go — the memory stayed full "
                              f"{MEMORY_WAIT_HOURS:g} h", flush=True)
                continue
            for d, (slot, _since) in list(_waiting.items()):
                del _waiting[d]
                _ran(d, slot)
                start_cycle(d, slot)
        except httpx.HTTPError as e:
            print(f"[Research] schedule: KG unreachable ({type(e).__name__})", flush=True)
        except Exception as e:
            print(f"[Research] schedule tick failed: {type(e).__name__}: {e}", flush=True)


def start_cycle(domain: str, slot: str) -> None:
    _cycling.add(domain)
    task = asyncio.get_running_loop().create_task(cycle(domain, slot))
    task.add_done_callback(lambda _t: _cycling.discard(domain))
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set[asyncio.Task] = set()   # running cycles (a task nothing refers to may be garbage-collected)


def _hours_since(last: dict | None) -> int:
    """The window a cycle reports on: since the previous cycle's report (or its start), 1 h to a week, in whole
    hours (the KG takes an int; rounded up, so nothing falls between two reports). A fixed 24 h reported the same
    changes again when a domain learns several times a day."""
    try:
        since = datetime.fromisoformat((last or {}).get("reported_at") or (last or {})["started_at"])
        if since.tzinfo is None:
            since = since.astimezone()
        return min(168, max(1, math.ceil((datetime.now().astimezone() - since).total_seconds() / 3600)))
    except (KeyError, TypeError, ValueError):
        return 24


async def cycle(d: str, slot: str) -> None:
    """One learning cycle (kg/core/learning.py): learn what's new and what the graph is unsure of, then keep what
    holds, forget what never did, sit the practice set and the exam (kg/core/exam.py), rewrite the synthesis and brief. Never raises (a cycle that dies is logged,
    not lost silently)."""
    try:
        await _cycle(d, slot)
    except Exception as e:
        print(f"[Research] {d}: learning cycle {slot} failed: {type(e).__name__}: {e}", flush=True)


async def _cycle(d: str, slot: str) -> None:
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
            except Exception as e:
                print(f"[Research] {d}: {label} failed ({type(e).__name__})", flush=True)
                return None

        previous = ((await step("learning", "GET", f"/domains/{d}/learning", lambda b: "ok")) or {}).get("last")
        # Learn: what's new (a first bootstrap for a new domain), then the questions for the graph's gaps.
        if not jobs.active(d):
            mode = "update" if jobs.bootstrapped(d) else "bootstrap"
            summary["runs"].append(jobs.submit(d, mode)["id"])
            if mode == "update":
                asked = await step("questions", "POST", f"/domains/{d}/learning/questions",
                                   lambda b: [q["question"] for q in b.get("questions", [])])
                for q in (asked or {}).get("questions", []):
                    summary["questions"].append(q)
                    summary["runs"].append(jobs.submit(d, "mission", q["question"])["id"])
        while jobs.active(d) and time.monotonic() - started < REPORT_WAIT:
            await asyncio.sleep(60)
        # Keep and forget: unconfirmed weak facts retired, the Head decides the review queue — so the report shows
        # what the graph now holds — then the briefing.
        forgot = await step("forget", "POST", f"/domains/{d}/learning/forget", json={"dry_run": False})
        await step("Head reviews", "POST", f"/domains/{d}/head-review", params={"minutes": REVIEW_MINUTES})
        # Practice: the questions the next cycle's research goes to (kg/core/exam.py). Then the exam: how much of the
        # charter the graph answers now — before the briefing, which reports it.
        await step("practice", "POST", f"/domains/{d}/exam",
                   lambda b: b.get("skipped") or b.get("busy") and "already running" or f"{b.get('score')}/100",
                   json={"wait": True, "kind": "practice"})
        sat = await step("exam", "POST", f"/domains/{d}/exam",
                         lambda b: b.get("skipped") or b.get("busy") and "already running" or f"{b.get('score')}/100",
                         json={"wait": True})
        if sat and "score" in sat:
            summary["exam"] = {k: sat[k] for k in ("score", "answered", "partial", "open", "total")}
        # The living review: each core question's page rewritten where its facts changed (kg/core/synthesis.py).
        await step("synthesis", "POST", f"/domains/{d}/synthesis",
                   lambda b: b.get("skipped") or f"{b.get('written')} rewritten, {b.get('unchanged')} unchanged",
                   json={"wait": True})
        hours = _hours_since(previous)
        summary["reported_at"] = jobs_now()
        await step(f"report ({hours} h)", "POST", f"/domains/{d}/reports", lambda b: b.get("headline", ""),
                   json={"hours": hours, "wait": True})
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


class Kickoff(BaseModel):
    questions: list[str] = Field(default_factory=list, max_length=12)


@app.post("/domains/{domain}/kickoff")
async def kickoff(domain: str, request: Kickoff):
    """A domain's first deep research, when its user confirms the research charter (the KG asks): a broad bootstrap
    unless the domain has one, then a mission for each core question. Queued like any research."""
    async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(domain), timeout=10) as kg:
        if (await kg.get(f"/domains/{domain}")).status_code == 404:
            raise HTTPException(status_code=404, detail=f"Domain '{domain}' does not exist")
    queued = []
    if not jobs.bootstrapped(domain) and not ((active := jobs.active(domain)) and active["mode"] == "bootstrap"):
        queued.append(jobs.submit(domain, "bootstrap"))
    for q in dict.fromkeys(q.strip() for q in request.questions if q.strip()):
        queued.append(jobs.submit(domain, "mission", q))
    return {"jobs": [{"id": j["id"], "mode": j["mode"], "question": j["question"]} for j in queued]}


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


def _service_only() -> None:
    """The crawler and the search are for Sovereign's own agents (the KG calls them with SOVEREIGN_TOKEN); the
    frontend never does. Open to any signed-in account, they were a fetch-anything proxy through /api/research/."""
    principal = auth.current.get()
    if not principal or principal.kind != "service":
        raise HTTPException(status_code=403, detail="Only Sovereign's own services may use this")


class FetchRequest(BaseModel):
    url: str
    max_chars: int = 12000


@app.post("/fetch")
async def fetch_now(request: FetchRequest):
    """Read one page now — a live sense for agents. Same rules as research runs: public URLs only,
    robots.txt respected, per-site pacing, rendered by the crawler."""
    _service_only()
    page = await fetch.fetch_page(request.url)
    return {"url": page.url, "title": page.title, "status": page.status, "error": page.error,
            "chars": len(page.markdown), "markdown": page.markdown[: max(0, min(request.max_chars, 50_000))]}


@app.get("/papers")
async def papers_now(q: str, limit: int = Query(6, ge=1, le=10)):
    """Scholarly papers now (OpenAlex, PubMed, arXiv — papers.py): each with its whole abstract and the text a reading
    of it gives the knowledge graph — for the KG's exam and the Head's search_papers."""
    _service_only()
    return {"papers": [{**papers.brief(p), "abstract": p.abstract, "markdown": p.markdown()}
                       for p in await papers.search(q, limit)]}


@app.get("/robots")
async def robots(url: str):
    """Whether robots.txt lets Sovereign read this URL (and it is a public address) — the KG's nightly self-check asks
    it; pages are checked by /fetch itself."""
    _service_only()
    if await asyncio.to_thread(fetch.validate_public_http_url, url, allow_private_addresses=False):
        return {"allowed": False, "reason": "not a public http(s) address"}
    refusal = await fetch.robots_refusal(url)
    return {"allowed": not refusal, "reason": refusal}


@app.get("/search")
async def search_now(q: str, time_range: Literal["day", "week", "month", "year"] | None = None,
                     limit: int = Query(8, ge=1, le=20)):
    """Web search now (local SearXNG, paced) — a live sense for agents; `time_range` for recent results.
    Several free engines ignore time ranges and then return nothing: such a search is retried without it."""
    _service_only()
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
    when = (last.get("finished_at") or last["created_at"])[:16].replace("T", " ") if last else ""
    return {
        "research": True,
        # Broken setup or a failing last run: the sidebar turns red instead of three nights of silent failures
        # (2026-10-10 → 12: every run failed on DeerFlow's config lookup while everything looked green).
        "config": runner.config_ok(),
        # Public: only that a run failed, and when — its error (internal URLs) and its domain (another tenant's)
        # are shown in the domain's own Research view.
        "last_failed": f"{last['mode']} run, {when} UTC" if last and last["status"] == "failed" else None,
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
