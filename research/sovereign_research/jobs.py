"""Research jobs: a persistent queue processed one at a time.

    research   DeerFlow reads the web (runner.run)
    ontology   bootstrap of a domain with no episodes yet: the grammar is derived from the pages it read
    ingesting  every readable page → KG episode (raw page, never the LLM report); failures recorded

The text of the pages read is kept on disk (pages/<job id>.json) until the KG has ingested it: a page the KG could
not take (down, timing out, a model step failed) is tried once more RETRY_MINUTES later, also after a restart, and a
job whose pages still fail ends "failed" with the reason.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sovereign_research import auth, fetch, missions, runner

KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
# Pages ingested at once (the KG reads chunks in parallel, INGEST_CONCURRENCY; writes stay one at a time).
INGEST_PAGES = int(os.getenv("RESEARCH_INGEST_PAGES", "3"))
DATA = Path(os.getenv("RESEARCH_DATA") or Path(__file__).resolve().parent.parent / ".data")
JOBS_FILE = DATA / "jobs.json"
PAGES_DIR = DATA / "pages"        # the text of read pages a job has not ingested yet
KEEP_JOBS = int(os.getenv("RESEARCH_KEEP_JOBS", "1000"))   # jobs.json keeps the newest; older ones go on load
# A run that fails reaching the KG (a timeout, a dropped connection — the machine briefly overloaded) before its
# research is done, or pages the KG could not ingest, are tried once more this many minutes later.
RETRY_MINUTES = float(os.getenv("RESEARCH_RETRY_MINUTES", "10"))
MODES = ("bootstrap", "update", "mission")
# Other apps' models in the same Ollama: past this many GB loaded, the lab waits until they're unloaded. 2026-11-07:
# a 27 GB model of the user's beside the lab's 22 GB filled the 64 GB machine — the model was killed mid-cycle
# (every exam answer "ConnectError") and Docker froze for two days.
OLLAMA_URL = (os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
OTHER_MODELS_GB = float(os.getenv("RESEARCH_OTHER_MODELS_GB", "8"))


def _local_models() -> set[str]:
    """The lab's language models served by Ollama (none when they all run on Claude): the KG's (LLM_MODEL and any
    agent's own LLM_MODEL_<AGENT>), the Head's and the research agent's."""
    names = {v.split(":", 1)[1] for k, v in os.environ.items()
             if (k in ("LLM_MODEL", "HEAD_MODEL") or k.startswith("LLM_MODEL_")) and v.startswith("ollama:")}
    try:
        provider, model = runner.research_model()
    except ValueError:   # a bad RESEARCH_MODEL: the run itself fails and says why
        return names
    if provider == "ollama":
        names.add(model)
    return names


def _lab_models() -> set[str]:
    names = _local_models() | {os.getenv("EMBEDDING_MODEL", "").removeprefix("ollama:")}
    return {n if ":" in n else f"{n}:latest" for n in names if n}


async def _kg_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            return (await c.get(f"{KG_URL}/health")).is_success
    except httpx.HTTPError:
        return False


async def others_loaded() -> list[str]:
    """The models in Ollama that aren't the lab's, when together they pass OTHER_MODELS_GB (else none). Nothing when
    Ollama can't say: that alone doesn't hold the lab back. Nothing when the lab's models run on Claude: only its small
    embedding model is local then, and other models can't crowd it out."""
    if not _local_models():
        return []
    url = OLLAMA_URL if "://" in OLLAMA_URL else f"http://{OLLAMA_URL}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            models = (await c.get(f"{url}/api/ps")).raise_for_status().json().get("models") or []
    except (httpx.HTTPError, ValueError):
        return []
    lab = _lab_models()
    others = [m for m in models if m.get("name") not in lab and m.get("model") not in lab]
    return [m.get("name", "?") for m in others] if sum(m.get("size") or 0 for m in others) > OTHER_MODELS_GB * 2**30 else []


class NothingRead(RuntimeError):
    """A run that read no page. `offline`: each page failed for want of a network, or the agent found none to open
    (the search engines unreachable too) — worth one more run, RETRY_MINUTES later."""

    def __init__(self, message: str, offline: bool) -> None:
        super().__init__(message)
        self.offline = offline


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._load()

    # ── persistence ────────────────────────────────────────────────────────
    def _load(self) -> None:
        if JOBS_FILE.exists():
            self.jobs = {j["id"]: j for j in json.loads(JOBS_FILE.read_text())}
        if len(self.jobs) > KEEP_JOBS:   # it grew without bound (every page's outcome, every step)
            newest = sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)[:KEEP_JOBS]
            self.jobs = {j["id"]: j for j in newest}
        for job in sorted(self.jobs.values(), key=lambda j: j["created_at"]):
            if job["status"] == "queued":       # never started, or waiting for its retry: run it now
                self.queue.put_nowait(job["id"])
            elif job["status"] in ("researching", "ontology", "ingesting") and self._kept(job):
                # ("researching" too: the pages are kept before the status moves on.)
                # The pages were read and kept: ingest what is left instead of losing them.
                job.update(status="queued", resume=True)
                job["steps"].append({"at": now(), "text": "interrupted by a restart — ingesting the pages kept"})
                self.queue.put_nowait(job["id"])
            elif job["status"] in ("researching", "ontology", "ingesting"):  # died mid-flight
                job.update(status="failed", error="interrupted by a service restart", finished_at=now())
        if PAGES_DIR.exists():   # kept pages of jobs gone or finished: nothing will read them again
            for f in PAGES_DIR.glob("*.json"):
                if (job := self.jobs.get(f.stem)) is None or job["status"] in ("done", "failed"):
                    f.unlink(missing_ok=True)
        self._save()

    def _save(self) -> None:
        DATA.mkdir(exist_ok=True)
        JOBS_FILE.write_text(json.dumps(list(self.jobs.values()), indent=1))

    # ── queries ────────────────────────────────────────────────────────────
    def list(self, domain: str | None = None, limit: int = 50) -> list[dict]:
        jobs = [j for j in self.jobs.values() if domain in (None, j["domain"])]
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return [self.public(j, pages=False) for j in jobs[:limit]]

    def get(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        return self.public(job) if job else None

    @staticmethod
    def public(job: dict, pages: bool = True) -> dict:
        out = {k: v for k, v in job.items() if k != "pages"}
        if pages:
            out["pages"] = job["pages"]
        return out

    def bootstrapped(self, domain: str) -> bool:
        """Whether a bootstrap of this domain finished with pages read. Any bootstrap used to count — a failed one
        too, so a domain whose first research failed was never bootstrapped again."""
        return any(j["domain"] == domain and j["mode"] == "bootstrap" and j["status"] == "done"
                   and (j.get("summary") or {}).get("pages_read") for j in self.jobs.values())

    def active(self, domain: str, mode: str | None = None) -> dict | None:
        """The domain's first unfinished job (of `mode`, when given). Asking for a bootstrap by mode: the first
        unfinished job may be a mission queued ahead of one."""
        return next((j for j in self.jobs.values() if j["domain"] == domain and mode in (None, j["mode"])
                     and j["status"] not in ("done", "failed")), None)

    # ── submit + worker ────────────────────────────────────────────────────
    def submit(self, domain: str, mode: str, question: str = "") -> dict:
        job = {
            "id": f"job_{uuid.uuid4().hex[:10]}", "domain": domain, "mode": mode, "question": question,
            "status": "queued", "created_at": now(), "started_at": None, "finished_at": None,
            "steps": [], "pages": [], "report": "", "error": "", "summary": {},
        }
        self.jobs[job["id"]] = job
        self._save()
        self.queue.put_nowait(job["id"])
        return self.public(job)

    async def _room(self, job: dict) -> None:
        """Wait until the KG answers, then while other apps' models fill the memory (others_loaded). 2026-11-11:
        after a reboot the research service came up before the KG, and nine queued runs failed to reach it at once,
        each spending its one retry."""
        noted = False
        while not await _kg_up():
            if not noted:
                self._step(job, "waiting for the KG to answer")
                noted = True
            await asyncio.sleep(30)
        noted = False
        while busy := await others_loaded():
            if not noted:
                self._step(job, f"waiting — other models fill the memory ({', '.join(busy)})")
                noted = True
            await asyncio.sleep(60)

    async def worker(self) -> None:
        while True:
            job = self.jobs[await self.queue.get()]
            try:
                await self._room(job)
                await self._run(job)
                if left := self._kept(job):
                    if not job.get("ingest_retried"):
                        self._later(job, f"{len(left)} page(s) not ingested — trying again in {RETRY_MINUTES:g} min",
                                    resume=True, ingest_retried=True)
                        continue
                    errors = [(p.get("ingest") or {}).get("error") for p in job["pages"] if p["url"] in left]
                    raise RuntimeError(f"{len(left)} of {job['summary'].get('pages_read', len(left))} page(s) could "
                                       f"not be ingested — {next((e for e in errors if e), 'no reason recorded')}")
                job["status"] = "done"
            except Exception as e:
                if self._kept(job) and not job.get("ingest_retried"):
                    # Read, but the ontology or ingest step broke off: the pages are on disk.
                    self._later(job, f"{type(e).__name__} after reading — ingesting the pages kept in "
                                f"{RETRY_MINUTES:g} min", resume=True, ingest_retried=True)
                    continue
                if isinstance(e, httpx.TransportError) and not job.get("retried") and not job["pages"]:
                    self._later(job, f"{type(e).__name__} reaching the KG — trying again in {RETRY_MINUTES:g} min",
                                retried=True)
                    continue
                if isinstance(e, NothingRead) and e.offline and not job.get("retried"):
                    job["pages"] = []
                    self._later(job, f"{e}, the network down — trying again in {RETRY_MINUTES:g} min", retried=True)
                    continue
                detail = str(e) or ("no answer in time — the KG or the machine was overloaded"
                                    if isinstance(e, httpx.TimeoutException) else "")
                job.update(status="failed", error=f"{type(e).__name__}: {detail}")
                self._keep(job, {})   # nothing reads a failed job's pages again (they'd pile up on disk)
            job["finished_at"] = now()
            self._save()

    def _later(self, job: dict, why: str, **flags) -> None:
        """Queue the job once more, RETRY_MINUTES from now."""
        job.update(status="queued", **flags)
        if not flags.get("resume"):
            job["started_at"] = None
        self._step(job, why)
        asyncio.get_running_loop().call_later(RETRY_MINUTES * 60, self.queue.put_nowait, job["id"])

    # ── the pages read, until ingested ─────────────────────────────────────
    @staticmethod
    def _kept_file(job: dict) -> Path:
        return PAGES_DIR / f"{job['id']}.json"

    def _kept(self, job: dict) -> dict[str, dict]:
        """url → {title, status, markdown} of the pages read and not ingested yet."""
        try:
            return json.loads(self._kept_file(job).read_text())
        except (OSError, ValueError):
            return {}

    def _keep(self, job: dict, pages: dict[str, dict]) -> None:
        path = self._kept_file(job)
        if pages:
            PAGES_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(pages, ensure_ascii=False))
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)

    def _step(self, job: dict, text: str) -> None:
        job["steps"].append({"at": now(), "text": text})
        job["steps"] = job["steps"][-200:]
        self._save()

    # ── the pipeline ───────────────────────────────────────────────────────
    async def _run(self, job: dict) -> None:
        if job.get("resume"):      # read before: what is left of the ontology step and the ingest
            job.update(started_at=job.get("started_at") or now())
            if job["mode"] == "bootstrap" and not job.get("ontology_done"):
                async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(job["domain"]), timeout=30) as kg:
                    r = await kg.get(f"/domains/{job['domain']}/episodes", params={"limit": 1})
                    await self._ontology(job, not r.raise_for_status().json()["episodes"])
            await self._ingest(job, self._kept(job))
            return
        domain = job["domain"]
        job.update(status="researching", started_at=now())
        async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(domain), timeout=30) as kg:
            # The domain as research should see it: its description plus the user's charter once confirmed.
            brief = (await kg.get(f"/domains/{domain}/charter")).raise_for_status().json()["brief"]
            episodes = (await kg.get(f"/domains/{domain}/episodes", params={"limit": 500})).raise_for_status().json()
        # Newest first (the KG's order): the prompt shows the most recent sources and the sites read most of late.
        recent = list(dict.fromkeys(e["source"] for e in episodes["episodes"]
                                    if str(e.get("source") or "").startswith("http")))

        if job["mode"] == "bootstrap":
            prompt = missions.bootstrap(domain, brief)
        elif job["mode"] == "update":
            prompt = missions.update(domain, brief, recent)
        else:
            prompt = missions.mission(domain, brief, job["question"])

        loop = asyncio.get_running_loop()
        on_step = lambda text: loop.call_soon_threadsafe(self._step, job, text)  # noqa: E731
        started = time.monotonic()
        report, pages = await asyncio.to_thread(runner.run, prompt, on_step, job["id"], domain)
        job["seconds"] = {"research": round(time.monotonic() - started), "ontology": 0, "ingest": 0}
        job["report"] = report
        readable = [p for p in pages if p.status != "failed"]
        if not readable:
            job["pages"] = [{"url": p.url, "title": p.title, "status": p.status, "error": p.error, "chars": 0}
                            for p in pages]
            raise NothingRead(
                "no page could be read — " + ("every page failed (see pages)" if pages else
                "the agent found nothing to open (search engines may be rate-limiting; "
                "try later, or set TAVILY_API_KEY / BRAVE_API_KEY in research/.env)"),
                offline=all(fetch.offline(p.error) for p in pages))
        job["pages"] = [
            {"url": p.url, "title": p.title, "status": p.status, "error": p.error, "chars": len(p.markdown),
             **({"tier": p.tier} if p.tier else {})}
            for p in pages
        ]
        # Until 2026-10-14 the text lived only in memory: pages the KG couldn't take were lost with "done".
        self._keep(job, {p.url: {"title": p.title, "status": p.status, "markdown": p.markdown, "tier": p.tier}
                         for p in readable})
        self._step(job, f"read {len(readable)} page(s), {len(pages) - len(readable)} failed")

        await self._ontology(job, not episodes["episodes"])
        await self._ingest(job, self._kept(job))

    async def _ontology(self, job: dict, empty: bool) -> None:
        """Bootstrap: the grammar from the pages read — only for a domain with nothing in it yet. A populated domain
        keeps its ontology (perhaps edited by hand): a bootstrap run again used to replace it."""
        if job["mode"] != "bootstrap" or job.get("ontology_done"):
            return
        if not empty:
            self._step(job, "the domain already has sources: keeping its ontology")
            job["ontology_done"] = True
            return
        job["status"] = "ontology"
        self._step(job, "deriving the domain's ontology from the pages read")
        started = time.monotonic()
        async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(job["domain"]),
                                     timeout=httpx.Timeout(3600, connect=10)) as kg:
            r = await kg.post(f"/domains/{job['domain']}/ontology/generate",
                              json={"corpus": [p["markdown"] for p in self._kept(job).values()], "wait": True})
        job.setdefault("seconds", {})["ontology"] = round(time.monotonic() - started)
        job["ontology_done"] = True
        if r.is_success:
            self._step(job, f"ontology: {', '.join(r.json()['ontology']['entity_types'])}")
        else:
            self._step(job, f"ontology generation failed ({r.status_code}); keeping the current one")

    async def _known(self, kg: httpx.AsyncClient, domain: str, urls: "list[str]") -> set[str]:  # list: a method here
        """The URLs the graph has read in full (any time — the newest 500 episodes missed older ones, which were
        read again as new episodes). A failed or unfinished read isn't known: it is read again."""
        known: set[str] = set()
        for i in range(0, len(urls), 500):
            try:
                r = await kg.post(f"/domains/{domain}/sources", json={"urls": urls[i:i + 500]})
                known.update(r.raise_for_status().json().get("known", []))
            except (httpx.HTTPError, ValueError) as e:
                # The KG dedupes identical text by itself; a page that changed is then read as a new episode.
                print(f"[Research] {domain}: known sources unavailable ({type(e).__name__})", flush=True)
        return known

    async def _ingest(self, job: dict, kept: dict[str, dict]) -> None:
        """Every kept page → the KG; a page leaves the kept file once the graph has it."""
        domain = job["domain"]
        job["status"] = "ingesting"
        self._save()
        by_url = {p["url"]: p for p in job["pages"]}
        seconds = job.setdefault("seconds", {"research": 0, "ontology": 0, "ingest": 0})
        # Long pages are several chunks of LLM work on the KG side (minutes each): wait up to an hour per call.
        async with httpx.AsyncClient(base_url=KG_URL, headers=auth.headers(domain),
                                     timeout=httpx.Timeout(3600, connect=10)) as kg:
            known = await self._known(kg, domain, list(kept))
            # Pages go to the KG side by side: it reads their chunks in parallel and writes them one at a time.
            gate = asyncio.Semaphore(INGEST_PAGES)
            ingest_started = time.monotonic()

            def ingested(url: str) -> None:
                kept.pop(url, None)
                self._keep(job, kept)

            async def ingest(url: str, page: dict):
                entry = by_url.setdefault(url, {"url": url, "title": page["title"], "status": page["status"],
                                                "error": "", "chars": len(page["markdown"])})
                if url in known:
                    entry["ingest"] = {"skipped": "already in the graph"}
                    ingested(url)
                    return
                async with gate:
                    self._step(job, f"ingesting {url}")
                    started = time.monotonic()
                    try:
                        r = await kg.post(f"/domains/{domain}/ingest", json={
                            "raw_text": page["markdown"], "source": url, "title": page["title"],
                            "content_status": page["status"], **({"tier": page["tier"]} if page.get("tier") else {}),
                            "hold_out": True,   # not a copy of an exam source under another address either
                        })
                        r.raise_for_status()
                        res = r.json()
                    except (httpx.HTTPError, ValueError) as e:
                        entry["ingest"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                    else:
                        if res.get("failed"):
                            # The KG took the call but a model step or a write failed: the text is read again.
                            reason = str(res.get("reason") or "no reason given")[:200]
                            entry["ingest"] = {"error": f"the KG could not read it: {reason}"}
                        elif res.get("held_out"):
                            entry["ingest"] = {"skipped": "a copy of a page the exam holds out"}
                            ingested(url)
                        elif res.get("duplicate") and res.get("pending"):
                            entry["ingest"] = {"skipped": "being read by another call", "pending": True}
                            ingested(url)
                        else:
                            entry["ingest"] = {k: res.get(k) for k in (
                                "duplicate", "entities_created", "entities_matched", "facts_created",
                                "facts_strengthened", "facts_invalidated", "facts_rejected", "review_items",
                                "chunks", "timings")}
                            ingested(url)
                    entry["ingest"]["seconds"] = round(time.monotonic() - started)
                    self._save()
            await asyncio.gather(*(ingest(u, p) for u, p in list(kept.items())))
            seconds["ingest"] = seconds.get("ingest", 0) + round(time.monotonic() - ingest_started)   # wall time

        read = [p for p in job["pages"] if p["status"] != "failed"]
        totals = {k: sum((p.get("ingest") or {}).get(k) or 0 for p in job["pages"]) for k in (
            "entities_created", "facts_created", "facts_strengthened", "facts_invalidated", "review_items")}
        job["summary"] = {"pages_read": len(read), "pages_failed": len(job["pages"]) - len(read),
                          "pages_not_ingested": len(kept), **totals, "seconds": dict(seconds)}
