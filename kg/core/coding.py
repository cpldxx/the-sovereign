"""Sensor requests: a need becomes a sensor.

backends   scout      many sources for the need, each one that works joins the sensor's group (core/scout.py) —
                      the default (SENSOR_BACKEND); with `group`, more sources for an existing sensor
           openhands  OpenHands in Docker (integrations/openhands.py): one module
           builtin    the KG's own Coder agent (agents/coder.py): search → docs → probe → write → test → fix;
                      also the fallback when OpenHands fails, and the only one for domains not owned by an admin
                      (OpenHands drives Docker on this machine)
           Measured (qwen3.6:35b, 2026-10-06, 2 needs × 2 backends): both 2/2. OpenHands 231 s / 228 s and picked
           the more standard sources (Yahoo chart API; Google News RSS); built-in 174 s / 98 s (Yahoo; an obscure
           "free news API"). Sensors are written rarely (slow path), so source quality wins over speed.
acceptance whatever a backend returns must pass the static check and a fresh sandbox run with its example
           parameters, returning a non-empty result — only then is it stored as an active sensor.
Requests run one at a time (they share the local model) and are kept in memory with their logs.
"""

import asyncio
import os
import time
import uuid

from agents import coder
from core import accounts, scout, sensors
from core.database import ArcadeDB, now
from domains.registry import load_domain
from integrations import openhands

BACKENDS = ("scout", "builtin", "openhands")
DEFAULT_BACKEND = os.getenv("SENSOR_BACKEND", "scout")

_jobs: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()
_lock = asyncio.Lock()


def jobs(domain: str | None = None) -> list[dict]:
    return sorted((j for j in _jobs.values() if domain in (None, j["domain"])), key=lambda j: j["created_at"],
                  reverse=True)


def job(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def submit(db: ArcadeDB, domain: str, need: str, backend: str | None = None, group: str | None = None) -> dict:
    load_domain(domain)
    backend = backend or DEFAULT_BACKEND
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    if backend == "openhands" and not accounts.trusted(domain):
        backend = "builtin"  # OpenHands drives Docker on this machine: only for the operator's own domains
    j = {"id": f"code_{uuid.uuid4().hex[:10]}", "domain": domain, "need": need.strip(), "backend": backend,
         "status": "queued", "created_at": now(), "finished_at": None, "seconds": None, "sensor": None,
         "note": "", "error": "", "log": [], "result": None, "code": None, "group": group, "candidates": [],
         "sources": None}
    _jobs[j["id"]] = j
    task = asyncio.get_running_loop().create_task(_run(db, j))
    _tasks.add(task)  # the loop keeps only weak references to tasks
    task.add_done_callback(_tasks.discard)
    return j


async def _attempt(j: dict, backend: str, description: str) -> tuple[str, dict]:
    """One backend's module, put through the acceptance test → (code, sample result). Raises SensorError."""
    if backend == "builtin":
        out = await coder.write_sensor(j["need"], j["domain"], description, j["log"])
        code, j["note"] = out.code, out.note
    else:
        code, tail = await openhands.write_sensor(j["need"], j["domain"], description)
        j["log"] += tail
    j["code"] = code
    if not code:
        raise sensors.SensorError(j["note"] or "the Coder wrote no module")
    j["status"] = "testing"
    meta, problems = sensors.inspect_code(code, j["domain"])
    if problems:
        raise sensors.SensorError("; ".join(problems))
    out = await sensors.run_code(code, sensors.examples(meta), j["domain"])
    if not out["ok"]:
        raise sensors.SensorError(f"sandbox test failed: {out['error']}")
    if not out["result"] or (isinstance(out["result"], dict) and not any(out["result"].values())):
        raise sensors.SensorError("sandbox test returned no data")
    return code, out["result"]


async def _run(db: ArcadeDB, j: dict) -> None:
    async with _lock:
        started = time.monotonic()
        j["status"] = "coding"
        try:
            description = load_domain(j["domain"])["config"].get("description") or j["domain"]
            if j["backend"] == "scout":
                await scout.discover(db, j, description)
                j["status"] = "done"
            else:
                try:
                    code, sample = await _attempt(j, j["backend"], description)
                except Exception as e:
                    if j["backend"] != "openhands":
                        raise
                    # OpenHands is heavier (Docker-in-Docker, a 10 GB runtime): when it fails, the built-in Coder tries.
                    j["log"].append(f"openhands failed ({type(e).__name__}: {str(e)[:200]}) — trying the built-in Coder")
                    j.update(backend="openhands→builtin", status="coding")
                    code, sample = await _attempt(j, "builtin", description)
                j["result"] = sensors.clip(sample)[:3000]
                author = "coder:" + ("builtin" if j["backend"].endswith("builtin") else j["backend"])
                stored = await sensors.save(db, j["domain"], code, j["need"], author, sample)
                j.update(status="done", sensor=stored["name"])
        except Exception as e:
            j.update(status="failed", error=f"{type(e).__name__}: {e}"[:1000])
        j.update(finished_at=now(), seconds=round(time.monotonic() - started))
