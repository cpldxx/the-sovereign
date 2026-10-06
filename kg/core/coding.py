"""Sensor requests: a Coder backend writes a module for a need, then the acceptance test decides.

backends   builtin    the KG's own Coder agent (agents/coder.py): search → docs → probe → write → test → fix
           openhands  OpenHands in Docker (integrations/openhands.py)
           CODER_BACKEND sets the default.
acceptance whatever a backend returns must pass the static check and a fresh sandbox run with its example
           parameters, returning a non-empty result — only then is it stored as an active sensor.
Requests run one at a time (they share the local model) and are kept in memory with their logs.
"""

import asyncio
import os
import time
import uuid

from agents import coder
from core import sensors
from core.database import ArcadeDB, now
from domains.registry import load_domain
from integrations import openhands

BACKENDS = ("builtin", "openhands")
DEFAULT_BACKEND = os.getenv("CODER_BACKEND", "builtin")

_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_lock = asyncio.Lock()


def jobs(domain: str | None = None) -> list[dict]:
    return sorted((j for j in _jobs.values() if domain in (None, j["domain"])), key=lambda j: j["created_at"],
                  reverse=True)


def job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def submit(db: ArcadeDB, domain: str, need: str, backend: str | None = None) -> dict:
    load_domain(domain)
    backend = backend or DEFAULT_BACKEND
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    j = {"id": f"code_{uuid.uuid4().hex[:10]}", "domain": domain, "need": need.strip(), "backend": backend,
         "status": "queued", "created_at": now(), "finished_at": None, "seconds": None, "sensor": None,
         "note": "", "error": "", "log": [], "result": None, "code": None}
    _jobs[j["id"]] = j
    task = asyncio.get_running_loop().create_task(_run(db, j))
    _tasks.add(task)  # the loop keeps only weak references to tasks
    task.add_done_callback(_tasks.discard)
    return j


async def _run(db: ArcadeDB, j: dict) -> None:
    async with _lock:
        started = time.monotonic()
        j["status"] = "coding"
        try:
            description = load_domain(j["domain"])["config"].get("description") or j["domain"]
            if j["backend"] == "builtin":
                out = await coder.write_sensor(j["need"], j["domain"], description, j["log"])
                code, j["note"] = out.code, out.note
            else:
                code, j["log"] = await openhands.write_sensor(j["need"], j["domain"], description)
            j["code"] = code
            if not code:
                raise sensors.SensorError(j["note"] or "the Coder wrote no module")
            j["status"] = "testing"
            meta, problems = sensors.inspect_code(code)
            if problems:
                raise sensors.SensorError("; ".join(problems))
            out = await sensors.run_code(code, sensors.examples(meta))
            if not out["ok"]:
                raise sensors.SensorError(f"sandbox test failed: {out['error']}")
            if not out["result"] or (isinstance(out["result"], dict) and not any(out["result"].values())):
                raise sensors.SensorError("sandbox test returned no data")
            j["result"] = sensors.clip(out["result"])[:3000]
            stored = await sensors.save(db, j["domain"], code, j["need"], f"coder:{j['backend']}", out["result"])
            j.update(status="done", sensor=stored["name"])
        except Exception as e:
            j.update(status="failed", error=f"{type(e).__name__}: {e}"[:1000])
        j.update(finished_at=now(), seconds=round(time.monotonic() - started))
