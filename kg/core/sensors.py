"""Sensors — live-data tools: the fast path's senses, checked right before acting.

The knowledge graph can be a day old; a sensor reads the world now (a price, a status page, today's headlines).
Each domain gets its own sensors, written by the Coder Agent for a need ("latest stock price for a ticker"),
tested, and stored in the domain's database. The Head reads them through `read_sensor`.

A sensor is one small Python module in a fixed shape (CONTRACT). It never runs in this process:
    static check  only whitelisted imports; no eval/exec/open/getattr, no dunder attributes; the module's
                  metadata is read with ast.literal_eval, never executed here
    sandbox       a fresh container per run (docker/sandbox): read-only, non-root, no capabilities,
                  256 MB / half a CPU / 64 processes, hard timeout, and an egress guard that only resolves
                  public internet addresses — the host and its services (this API, Ollama) are unreachable
"""

import ast
import asyncio
import json
import os
import re
from pathlib import Path

from core import database as kgdb
from core.database import ArcadeDB, now

IMAGE = os.getenv("SANDBOX_IMAGE", "sovereign-sandbox:2")
SANDBOX_DIR = Path(__file__).resolve().parents[2] / "docker" / "sandbox"
RUN_TIMEOUT = 40          # seconds per container (the runner gives run() 25 s)
MAX_CODE = 20_000
MAX_RESULT = 8_000        # characters of a reading handed to agents
SENSOR_JSON = ("params", "sample")

ALLOWED_IMPORTS = {"httpx", "json", "re", "math", "statistics", "datetime", "time", "urllib.parse",
                   "xml.etree.ElementTree", "html", "csv", "io", "zoneinfo", "decimal"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "input",
                   "breakpoint", "getattr", "setattr", "delattr", "memoryview", "help", "exit", "quit"}
_NAME = re.compile(r"[a-z][a-z0-9_]{2,40}")
_TYPES = {"string", "number", "integer", "boolean"}

def available_secrets() -> list[str]:
    """API keys the owner lets sensors use: names listed in SENSOR_SECRETS that have a value in the environment."""
    names = [n.strip() for n in os.getenv("SENSOR_SECRETS", "").split(",") if n.strip()]
    return [n for n in names if os.getenv(n)]


def contract() -> str:
    """The module contract, with the API keys currently available to sensors."""
    keys = available_secrets()
    return CONTRACT + (
        f"\n- API keys available: {', '.join(keys)}. Prefer an official API: when one of these keys is for it, declare "
        "SECRETS = [\"NAME\"] and read it with secret(\"NAME\") (provided at run time, no import). Never return or "
        "print a key." if keys else "")


CONTRACT = f'''Write ONE Python module (sensor.py) that reads live data from the public internet, in exactly this shape:

NAME = "snake_case_name"
DESCRIPTION = "What it returns, in one sentence — agents read this to decide when to use it"
PARAMS = {{"symbol": {{"type": "string", "description": "Stock ticker, e.g. NVDA", "example": "NVDA"}}}}

def run(symbol: str) -> dict:
    ...  # fetch with httpx (synchronous: httpx.get / httpx.Client), parse, return a small dict

Rules:
- Imports only from: {", ".join(sorted(ALLOWED_IMPORTS))}.
- No file access, no eval/exec/getattr, no dunder attributes. No API keys: use free public endpoints that need none.
- PARAMS: every parameter of run() with a type ({", ".join(sorted(_TYPES))}), a description and an example value.
- Always pass timeout=15 and a browser-like User-Agent header. Follow redirects.
- Return a small JSON-serializable dict: only the useful fields (at most 50 items), plus "source" (the URL read)
  and the data's own timestamp when the source gives one.
- If the data can't be fetched or parsed, raise an exception with a clear message — never return made-up values.'''


class SensorError(ValueError):
    pass


# ── Checking a module ──────────────────────────────────────────────────────

def inspect_code(code: str) -> tuple[dict, list[str]]:
    """(metadata {name, description, params}, problems). Reads the module without running it."""
    if len(code) > MAX_CODE:
        return {}, [f"module longer than {MAX_CODE} characters"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {}, [f"syntax error: {e}"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f"import {a.name} is not allowed" for a in node.names if a.name not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                problems.append(f"from {node.module} import … is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"{node.id} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"dunder attribute .{node.attr} is not allowed")
    meta: dict = {}
    run_args: list[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("NAME", "DESCRIPTION", "PARAMS", "SECRETS"):
                try:
                    meta[node.targets[0].id.lower()] = ast.literal_eval(node.value)
                except ValueError:
                    problems.append(f"{node.targets[0].id} must be a literal")
        elif isinstance(node, ast.FunctionDef) and node.name == "run":
            run_args = [a.arg for a in node.args.args + node.args.kwonlyargs]
    if not isinstance(meta.get("name"), str) or not _NAME.fullmatch(meta["name"]):
        problems.append("NAME must be a snake_case string (3-41 chars)")
    if not isinstance(meta.get("description"), str) or not meta["description"].strip():
        problems.append("DESCRIPTION must be a non-empty string")
    params = meta.get("params")
    if not isinstance(params, dict):
        problems.append("PARAMS must be a dict")
        params = {}
    for name, spec in params.items():
        if not isinstance(spec, dict) or spec.get("type") not in _TYPES or "example" not in spec:
            problems.append(f"PARAMS[{name!r}] needs a type ({', '.join(sorted(_TYPES))}), a description, an example")
    secrets = meta.get("secrets") or []
    if not isinstance(secrets, list) or not all(isinstance(x, str) for x in secrets):
        problems.append("SECRETS must be a list of key names")
    else:
        problems += [f"secret {x} is not available (SENSOR_SECRETS in kg/.env)" for x in secrets
                     if x not in available_secrets()]
    if run_args is None:
        problems.append("a module-level function run(...) is required")
    elif set(run_args) != set(params):
        problems.append(f"run() arguments {run_args} must match PARAMS {list(params)}")
    return meta, problems


def examples(meta: dict) -> dict:
    return {k: v.get("example") for k, v in (meta.get("params") or {}).items()}


# ── Running in the sandbox ─────────────────────────────────────────────────

_image_ready = False


async def _docker(*args: str, stdin: bytes | None = None, timeout: float = RUN_TIMEOUT) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out, err


async def _ensure_image() -> None:
    global _image_ready
    if _image_ready:
        return
    code, _, _ = await _docker("image", "inspect", IMAGE, timeout=30)
    if code != 0:
        code, _, err = await _docker("build", "-q", "-t", IMAGE, str(SANDBOX_DIR), timeout=600)
        if code != 0:
            raise SensorError(f"Could not build the sandbox image: {err.decode()[-500:]}")
    _image_ready = True


async def run_code(code: str, params: dict) -> dict:
    """Run a checked module's run(**params) in a fresh sandbox container. Returns {"ok", "result" | "error"}."""
    meta, problems = inspect_code(code)
    if problems:
        return {"ok": False, "error": "; ".join(problems)}
    await _ensure_image()
    try:
        _, out, err = await _docker(
            "run", "--rm", "-i", "--read-only", "--tmpfs", "/tmp:rw,size=16m", "--memory", "256m", "--cpus", "0.5",
            "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--add-host", "host.docker.internal:127.0.0.1", "--add-host", "gateway.docker.internal:127.0.0.1",
            IMAGE, stdin=json.dumps({"code": code, "params": params, "secrets": {
                name: os.environ[name] for name in (meta.get("secrets") or []) if name in available_secrets()}}).encode(),
        )
    except TimeoutError:
        return {"ok": False, "error": f"timed out after {RUN_TIMEOUT} s"}
    try:
        return json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"sandbox failed: {err.decode()[-500:] or 'no output'}"}


def clip(result) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT else text[:MAX_RESULT] + "…(truncated)"


# ── Stored sensors ─────────────────────────────────────────────────────────

async def list_sensors(db: ArcadeDB, domain: str, *, with_code: bool = False) -> list[dict]:
    rows = await kgdb.find_docs(db, domain, "Sensor", SENSOR_JSON, order="name ASC")
    return [r if with_code else {k: v for k, v in r.items() if k != "code"} for r in rows]


async def get_sensor(db: ArcadeDB, domain: str, name: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Sensor", SENSOR_JSON, "name = :name", name=name)
    return rows[0] if rows else None


async def save(db: ArcadeDB, domain: str, code: str, need: str, author: str, sample: dict) -> dict:
    """Store a tested module (replacing a sensor of the same name)."""
    meta, problems = inspect_code(code)
    if problems:
        raise SensorError("; ".join(problems))
    doc = {"name": meta["name"], "description": meta["description"].strip(), "params": meta["params"],
           "code": code, "need": need, "author": author, "status": "active", "sample": clip(sample)[:3000],
           "tested_at": now(), "last_run_at": None, "last_ok": True}
    if existing := await get_sensor(db, domain, meta["name"]):
        await kgdb.update_doc(db, domain, "Sensor", existing["uid"], doc, SENSOR_JSON)
        return {**existing, **doc}
    return await kgdb.insert_doc(db, domain, "Sensor", doc, SENSOR_JSON)


async def read(db: ArcadeDB, domain: str, name: str, params: dict | None = None) -> dict:
    """Run a stored sensor now. Missing parameters take their example values."""
    sensor = await get_sensor(db, domain, name)
    if not sensor or sensor["status"] != "active":
        raise SensorError(f"No active sensor {name!r}")
    unknown = set(params or {}) - set(sensor["params"])
    if unknown:
        raise SensorError(f"Unknown parameters {sorted(unknown)}; {name} takes {list(sensor['params'])}")
    args = {**{k: v.get("example") for k, v in sensor["params"].items()}, **(params or {})}
    out = await run_code(sensor["code"], args)
    await kgdb.update_doc(db, domain, "Sensor", sensor["uid"], {"last_run_at": now(), "last_ok": out["ok"]})
    return {"sensor": name, "params": args, "read_at": now(), **out}


async def remove(db: ArcadeDB, domain: str, name: str) -> None:
    if not (sensor := await get_sensor(db, domain, name)):
        raise SensorError(f"No sensor {name!r}")
    await kgdb.delete_doc(db, domain, "Sensor", sensor["uid"])
