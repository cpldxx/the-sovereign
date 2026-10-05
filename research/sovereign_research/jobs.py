"""Research jobs: a persistent queue processed one at a time.

    research   DeerFlow reads the web (runner.run)
    ontology   bootstrap only: the domain's grammar is regenerated from the pages it read
    ingesting  every readable page → KG episode (raw page, never the LLM report); failures recorded
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sovereign_research import missions, runner

KG_URL = os.getenv("SOVEREIGN_KG_URL", "http://localhost:8080").rstrip("/")
DATA = Path(__file__).resolve().parent.parent / ".data"
JOBS_FILE = DATA / "jobs.json"
MODES = ("bootstrap", "update", "mission")


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
        for job in sorted(self.jobs.values(), key=lambda j: j["created_at"]):
            if job["status"] == "queued":       # never started: run it now
                self.queue.put_nowait(job["id"])
            elif job["status"] in ("researching", "ontology", "ingesting"):  # died mid-flight
                job.update(status="failed", error="interrupted by a service restart", finished_at=now())
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

    def last(self, domain: str, mode: str) -> dict | None:
        done = [j for j in self.jobs.values() if j["domain"] == domain and j["mode"] == mode]
        return max(done, key=lambda j: j["created_at"]) if done else None

    def active(self, domain: str) -> dict | None:
        return next((j for j in self.jobs.values()
                     if j["domain"] == domain and j["status"] not in ("done", "failed")), None)

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

    async def worker(self) -> None:
        while True:
            job = self.jobs[await self.queue.get()]
            try:
                await self._run(job)
                job["status"] = "done"
            except Exception as e:
                job.update(status="failed", error=f"{type(e).__name__}: {e}")
            job["finished_at"] = now()
            self._save()

    def _step(self, job: dict, text: str) -> None:
        job["steps"].append({"at": now(), "text": text})
        job["steps"] = job["steps"][-200:]
        self._save()

    # ── the pipeline ───────────────────────────────────────────────────────
    async def _run(self, job: dict) -> None:
        domain = job["domain"]
        job.update(status="researching", started_at=now())
        async with httpx.AsyncClient(base_url=KG_URL, timeout=30) as kg:
            detail = (await kg.get(f"/domains/{domain}")).raise_for_status().json()
            episodes = (await kg.get(f"/domains/{domain}/episodes", params={"limit": 500})).json()["episodes"]
        description = detail["config"].get("description") or domain
        known = {e["source"] for e in episodes}

        if job["mode"] == "bootstrap":
            prompt = missions.bootstrap(domain, description)
        elif job["mode"] == "update":
            prompt = missions.update(domain, description, sorted(known, key=lambda u: u)[:200])
        else:
            prompt = missions.mission(domain, description, job["question"])

        loop = asyncio.get_running_loop()
        on_step = lambda text: loop.call_soon_threadsafe(self._step, job, text)  # noqa: E731
        report, pages = await asyncio.to_thread(runner.run, prompt, on_step)
        job["report"] = report
        readable = [p for p in pages if p.status != "failed"]
        job["pages"] = [
            {"url": p.url, "title": p.title, "status": p.status, "error": p.error, "chars": len(p.markdown)}
            for p in pages
        ]
        self._step(job, f"read {len(readable)} page(s), {len(pages) - len(readable)} failed")

        # Long pages are several chunks of LLM work on the KG side (minutes each): wait up to an hour per call.
        async with httpx.AsyncClient(base_url=KG_URL, timeout=httpx.Timeout(3600, connect=10)) as kg:
            if job["mode"] == "bootstrap" and readable:
                job["status"] = "ontology"
                self._step(job, "deriving the domain's ontology from the pages read")
                r = await kg.post(f"/domains/{domain}/ontology/generate",
                                  json={"corpus": [p.markdown for p in readable], "wait": True})
                if r.is_success:
                    self._step(job, f"ontology: {', '.join(r.json()['ontology']['entity_types'])}")
                else:
                    self._step(job, f"ontology generation failed ({r.status_code}); keeping the current one")

            job["status"] = "ingesting"
            self._save()
            by_url = {p["url"]: p for p in job["pages"]}
            for page in readable:
                entry = by_url[page.url]
                if page.url in known:
                    entry["ingest"] = {"skipped": "already in the graph"}
                    continue
                self._step(job, f"ingesting {page.url}")
                try:
                    r = await kg.post(f"/domains/{domain}/ingest", json={
                        "raw_text": page.markdown, "source": page.url, "title": page.title,
                        "content_status": page.status,
                    })
                    r.raise_for_status()
                    res = r.json()
                    entry["ingest"] = {k: res.get(k) for k in (
                        "duplicate", "entities_created", "entities_matched", "facts_created",
                        "facts_strengthened", "facts_invalidated", "facts_rejected", "review_items")}
                    known.add(page.url)
                except httpx.HTTPError as e:
                    entry["ingest"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                self._save()

        totals = {k: sum((p.get("ingest") or {}).get(k) or 0 for p in job["pages"]) for k in (
            "entities_created", "facts_created", "facts_strengthened", "facts_invalidated", "review_items")}
        job["summary"] = {"pages_read": len(readable), "pages_failed": len(pages) - len(readable), **totals}
