"""Sensors — live-data tools: the fast path's senses, checked right before acting.

The knowledge graph can be a day old; a sensor reads the world now (a price, a status page, today's headlines).
Each domain gets its own sensors, written for a need ("latest stock price for a ticker"), tested, and stored in the
domain's database. The Head reads them through `read_sensor`.

A sensor is a GROUP of sources for one need — every source a module with the same parameters and fields: an API, or a
web page opened in a real browser (PAGES, for sites without an API). Reading a group tries its sources best first
(success rate, then speed) and falls back to the next when one fails; `verify_field` reads a second source and
compares. Scout (core/scout.py) collects the sources; sensors written before groups are groups of one.

A sensor is one small Python module in a fixed shape (CONTRACT). It never runs in this process:
    static check  only whitelisted imports; no eval/exec/open/getattr, no dunder attributes; the module's
                  metadata is read with ast.literal_eval, never executed here
    sandbox       a fresh container per run (docker/sandbox): read-only, non-root, no capabilities,
                  256 MB / half a CPU / 64 processes, hard timeout, and an egress guard that only resolves
                  public internet addresses — the host and its services (this API, Ollama) are unreachable
"""

import ast
import asyncio
import functools
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, create_model

from core import accounts, auth
from core import database as kgdb
from core.database import ArcadeDB, now

IMAGE = os.getenv("SANDBOX_IMAGE", "sovereign-sandbox:4")
RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
SANDBOX_DIR = Path(__file__).resolve().parents[2] / "docker" / "sandbox"
RUN_TIMEOUT = 40          # seconds per container (the runner gives run() 25 s)
MAX_CODE = 20_000
MAX_RESULT = 8_000        # characters of a reading handed to agents
SENSOR_JSON = ("params", "sample", "key_fields", "misses", "schema")
PAGE_CHARS = 40_000       # text of a rendered page handed to a module
AGREE = 0.01              # two sources agree when their values are within 1 % (live quotes: < 0.2 % apart)
HISTORY = 20              # outcomes kept per source for its success rate
MISS_HOURS = 24           # a source that failed for these parameters is tried after the others for this long
COOLDOWN_HOURS = 6        # a source that refused us (bot protection, 403/429, robots.txt) rests, doubling to 48 h
PAGE_TTL = 60             # seconds a rendered page is reused (a check and its verification don't open it twice)
_BLOCKED = re.compile(r"anti-bot|captcha|challenge|robots\.txt|\b403\b|\b429\b|rate.?limit|too many requests", re.I)

ALLOWED_IMPORTS = {"httpx", "json", "re", "math", "statistics", "datetime", "time", "urllib.parse",
                   "xml.etree.ElementTree", "html", "csv", "io", "zoneinfo", "decimal"}
FORBIDDEN_NAMES = {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "input",
                   "breakpoint", "getattr", "setattr", "delattr", "memoryview", "help", "exit", "quit"}
_NAME = re.compile(r"[a-z][a-z0-9_]{2,40}")
_TYPES = {"string", "number", "integer", "boolean"}

def available_secrets(domain: str | None = None) -> list[str]:
    """API keys the operator lets sensors use: names listed in SENSOR_SECRETS that have a value in the environment —
    for the operator's own (trusted) domains only; other accounts' sensors run without them."""
    if domain and not accounts.trusted(domain):
        return []
    names = [n.strip() for n in os.getenv("SENSOR_SECRETS", "").split(",") if n.strip()]
    return [n for n in names if os.getenv(n)]


def contract(domain: str | None = None) -> str:
    """The module contract, with the API keys available to the domain's sensors."""
    keys = available_secrets(domain)
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
- A source without an API (a web page, JavaScript pages too): declare PAGES = {{"main": "https://site/quote/{{symbol}}"}}
  (URL templates whose placeholders are parameters of run()). Each page is opened in a real browser before run()
  starts (robots.txt respected); page("main") returns its text as markdown. Parse it with re / str methods (no httpx
  needed for it); anchor on the labels next to the value, never "the first number". page() raises if the site
  refused (robots.txt, bot protection).
- Return a small JSON-serializable dict: only the useful fields (at most 50 items), plus "source" (the URL read)
  and the data's own timestamp when the source gives one.
- If the data can't be fetched or parsed, raise an exception with a clear message — never return made-up values.'''


class SensorError(ValueError):
    pass


# ── Checking a module ──────────────────────────────────────────────────────

def inspect_code(code: str, domain: str | None = None) -> tuple[dict, list[str]]:
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
            if node.targets[0].id in ("NAME", "DESCRIPTION", "PARAMS", "SECRETS", "PAGES"):
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
                     if x not in available_secrets(domain)]
    pages = meta.get("pages") or {}
    if not isinstance(pages, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pages.items()):
        problems.append("PAGES must map names to URL templates")
    else:
        for name, url in pages.items():
            if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
                problems.append(f"PAGES[{name!r}] must be an http(s) URL")
            problems += [f"PAGES[{name!r}] uses {{{p}}}, which is not a parameter"
                         for p, _ in _PLACEHOLDER.findall(url) if p not in params]
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


_PLACEHOLDER = re.compile(r"{(\w+)(?:\|(lower|upper))?}")


def fill(template: str, params: dict) -> str:
    """A URL template with the parameters filled in (URL-encoded); {symbol|lower} / {symbol|upper} change the case."""
    def value(m: re.Match) -> str:
        v = str(params.get(m[1], ""))
        return quote(v.lower() if m[2] == "lower" else v.upper() if m[2] == "upper" else v, safe="")
    return _PLACEHOLDER.sub(value, template)


_pages: dict[str, tuple[float, dict]] = {}


async def render(url: str, domain: str | None = None) -> dict:
    """A web page opened in a browser by the research service (robots.txt, per-site pacing, public addresses only)
    → {"text"} or {"error"}. Reused for PAGE_TTL seconds."""
    if (hit := _pages.get(url)) and time.monotonic() - hit[0] < PAGE_TTL:
        return hit[1]
    page = await _render(url, domain)
    if "text" in page:
        if len(_pages) > 200:
            _pages.clear()
        _pages[url] = (time.monotonic(), page)
    return page


async def _render(url: str, domain: str | None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120, headers=auth.headers(domain)) as client:
            r = await client.post(f"{RESEARCH_URL}/fetch", json={"url": url, "max_chars": PAGE_CHARS})
        page = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"research service unreachable ({type(e).__name__})"}
    if r.is_error or page.get("status") == "failed":
        return {"error": page.get("error") or page.get("detail") or f"HTTP {r.status_code}"}
    return {"text": page.get("markdown") or ""}


async def robots_allows(url: str, domain: str | None = None) -> bool:
    """robots.txt for an API endpoint too (pages are checked by render)."""
    try:
        async with httpx.AsyncClient(timeout=30, headers=auth.headers(domain)) as client:
            r = await client.get(f"{RESEARCH_URL}/robots", params={"url": url})
        return bool(r.json().get("allowed")) if r.is_success else False
    except (httpx.HTTPError, ValueError):
        return False


PROBE = '''import httpx
NAME = "probe"
DESCRIPTION = "probe"
PARAMS = {"url": {"type": "string", "description": "url", "example": ""}}

def run(url: str) -> dict:
    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
    return {"status": r.status_code, "content_type": r.headers.get("content-type", ""), "body": r.text[:6000]}
'''


async def probe(url: str) -> dict:
    """GET a URL from the sandbox → {"ok", "result": {status, content_type, body}} (no robots check: see
    robots_allows)."""
    return await run_code(PROBE, {"url": url})


async def run_code(code: str, params: dict, domain: str | None = None) -> dict:
    """Run a checked module's run(**params) in a fresh sandbox container, with the API keys it declares if the
    domain may use them and its PAGES opened first. Returns {"ok", "result" | "error"}."""
    meta, problems = inspect_code(code, domain)
    if problems:
        return {"ok": False, "error": "; ".join(problems)}
    await _ensure_image()
    names = list(meta.get("pages") or {})
    rendered = await asyncio.gather(*(render(fill(meta["pages"][n], params), domain) for n in names))
    pages = dict(zip(names, rendered))
    try:
        _, out, err = await _docker(
            "run", "--rm", "-i", "--read-only", "--tmpfs", "/tmp:rw,size=16m", "--memory", "256m", "--cpus", "0.5",
            "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--add-host", "host.docker.internal:127.0.0.1", "--add-host", "gateway.docker.internal:127.0.0.1",
            IMAGE, stdin=json.dumps({"code": code, "params": params, "pages": pages, "secrets": {
                name: os.environ[name] for name in (meta.get("secrets") or []) if name in available_secrets(domain)}}
            ).encode(),
        )
    except TimeoutError:
        return {"ok": False, "error": f"timed out after {RUN_TIMEOUT} s"}
    try:
        return json.loads(out.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"sandbox failed: {err.decode()[-500:] or 'no output'}"}


def shrink(value, depth: int = 0):
    """A reading cut down to a small valid JSON value: lists to 3 items, strings to 200 characters."""
    if isinstance(value, dict):
        return {k: shrink(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [shrink(v, depth + 1) for v in value[:3]]
    if isinstance(value, str):
        return value[:200]
    return value


def sample_of(sensor: dict):
    """A stored sample as data. Samples stored before shrink() were cut mid-JSON: their top-level keys are kept."""
    text = sensor.get("sample") or ""
    try:
        return json.loads(text) if text else None
    except ValueError:
        depth, keys = 0, []
        for m in re.finditer(r'[{}\[\]]|"(\w+)"\s*:', text):
            if m[0] in "{[":
                depth += 1
            elif m[0] in "}]":
                depth -= 1
            elif depth == 1:
                keys.append(m[1])
        return dict.fromkeys(keys) or None


def clip(result) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT else text[:MAX_RESULT] + "…(truncated)"


# ── Stored sensors and groups ──────────────────────────────────────────────

def url_of(sensor: dict) -> str:
    """The URL template a source reads: stored by the Scout, or a page module's PAGES."""
    if sensor.get("url"):
        return sensor["url"]
    meta, _ = inspect_code(sensor.get("code") or "")
    return next(iter((meta.get("pages") or {}).values()), "")


def group_of(sensor: dict) -> str:
    return sensor.get("group") or sensor["name"]


def host_of(sensor: dict) -> str:
    """The site a source reads (sensors from before groups: from the "source" URL in their sample)."""
    host = sensor.get("host") or ""
    if not host:
        try:
            host = urlsplit(str(json.loads(sensor.get("sample") or "{}").get("source", ""))).netloc
        except (ValueError, AttributeError):
            host = ""
    return host.lower().removeprefix("www.")


def score(sensor: dict) -> float:
    """Recent success rate (an untried source counts as even)."""
    history = sensor.get("history") or ""
    return (history.count("1") + 1) / (len(history) + 2)


def _order(members: list[dict]) -> list[dict]:
    return sorted(members, key=lambda m: (-score(m), m.get("avg_seconds") or 99.0))


def _params_key(args: dict) -> str:
    return json.dumps(args, sort_keys=True, default=str)[:200]


def cooling(member: dict) -> bool:
    """Resting after it refused us — not to be asked again before cooldown_until."""
    return (member.get("cooldown_until") or "") > now()


def _missed(member: dict, args: dict) -> bool:
    """Failed for exactly these parameters lately (an exchange-specific page and an NYSE ticker)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=MISS_HOURS)).isoformat(timespec="seconds")
    return (member.get("misses") or {}).get(_params_key(args), "") > since


def _route(members: list[dict], args: dict) -> list[dict]:
    """Best first; those that failed for these parameters lately after the rest; resting ones last (tried only if
    nothing else answers — except a robots.txt refusal, which is never retried while it rests)."""
    ready = [m for m in members if not cooling(m)]
    resting = [m for m in members if cooling(m) and "robots" not in (m.get("last_error") or "")]
    return [m for m in ready if not _missed(m, args)] + [m for m in ready if _missed(m, args)] + resting


async def list_sensors(db: ArcadeDB, domain: str, *, with_code: bool = False) -> list[dict]:
    rows = await kgdb.find_docs(db, domain, "Sensor", SENSOR_JSON, order="name ASC")
    return [r if with_code else {k: v for k, v in r.items() if k != "code"} for r in rows]


async def get_sensor(db: ArcadeDB, domain: str, name: str) -> dict | None:
    rows = await kgdb.find_docs(db, domain, "Sensor", SENSOR_JSON, "name = :name", name=name)
    return rows[0] if rows else None


async def members(db: ArcadeDB, domain: str, name: str, *, with_code: bool = True) -> list[dict]:
    """The active sources of a group, best first — or the one sensor called `name`."""
    rows = [s for s in await list_sensors(db, domain, with_code=with_code) if s["status"] == "active"]
    group = [s for s in rows if group_of(s) == name]
    return _order(group or [s for s in rows if s["name"] == name])


async def groups(db: ArcadeDB, domain: str) -> list[dict]:
    """What agents see: one entry per group with its best source's description, parameters and sample."""
    by: dict[str, list[dict]] = {}
    for s in await list_sensors(db, domain):
        if s["status"] == "active":
            by.setdefault(group_of(s), []).append(s)
    out = []
    for name, ms in sorted(by.items()):
        ms = _order(ms)
        best = ms[0]
        out.append({
            "name": name, "description": best["description"], "params": best["params"], "need": best.get("need"),
            "sample": best.get("sample"), "key_fields": best.get("key_fields") or [],
            "tested_at": max(m.get("tested_at") or "" for m in ms),
            "last_run_at": max((m.get("last_run_at") or "" for m in ms), default="") or None,
            "last_ok": any(m.get("last_ok") for m in ms),
            "working": sum(1 for m in ms if m.get("last_ok") is not False and not cooling(m)),
            "sources": [{"name": m["name"], "host": host_of(m), "kind": m.get("kind") or "api",
                         "last_ok": m.get("last_ok"), "score": round(score(m), 2), "avg_seconds": m.get("avg_seconds"),
                         "last_error": m.get("last_error"), "author": m.get("author"),
                         "cooldown_until": m.get("cooldown_until") if cooling(m) else None} for m in ms],
        })
    return out


# ── Shape: whichever source answers, agents get the same checked fields ────────────────────────────────────────

_JUNK = re.compile(r"\]\(|\[skip|skip to (main )?content|navigation-container|accept (all )?cookies", re.I)


def _number(v):
    """"1,234.5", "$237.05", "−2.31" (a Unicode minus), "0.92%" → float; anything else is left for pydantic to refuse."""
    if isinstance(v, str):
        cleaned = v.strip().replace("\u2212", "-").replace(",", "").replace("$", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return v
    return v


def _text(v: str) -> str:
    if len(v) > 300 or _JUNK.search(v) or v.count("\n") > 3:
        raise ValueError("not a clean value — page markup or navigation text")
    return v


Number = Annotated[float, BeforeValidator(_number)]
Text = Annotated[str, AfterValidator(_text)]
_KINDS = {"number": Number, "string": Text, "boolean": bool, "list": list, "object": dict}


def _kind_of(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "list" if isinstance(value, list) else "object" if isinstance(value, dict) else "string"


def schema_of(member: dict) -> list[dict]:
    """The group's fields ({name, type, required}): the Scout's, or — for a sensor from before — its sample's."""
    if member.get("schema"):
        return member["schema"]
    sample = sample_of(member)
    if not isinstance(sample, dict):
        return []
    keys = set(member.get("key_fields") or [])
    return [{"name": k, "type": _kind_of(v) if v is not None else "string", "required": k in keys}
            for k, v in sample.items() if k != "source"]


@functools.lru_cache(maxsize=256)
def _model(fields: tuple) -> type[BaseModel]:
    spec = {f"f{i}": ((_KINDS[kind] if required else _KINDS[kind] | None), Field(... if required else None, alias=name))
            for i, (name, kind, required) in enumerate(fields)}
    return create_model("Reading", __config__=ConfigDict(extra="ignore", populate_by_name=True), **spec)


def conform(result, schema: list[dict]) -> tuple[dict | None, str]:
    """A source's answer checked against the group's fields (pydantic): numbers parsed ("$1,234.5"), required fields
    present, text free of page markup; fields outside the shape — or optional ones that don't pass — dropped.
    (answer, "") or (None, why)."""
    if not schema:
        return result, ""
    if not isinstance(result, dict):
        return None, "the answer is not a dict"
    model = _model(tuple((f["name"], f.get("type") or "string", bool(f.get("required"))) for f in schema))
    required = {f["name"] for f in schema if f.get("required")}
    result = dict(result)
    for _ in range(2):   # a bad optional field is dropped; a bad required one fails the answer
        try:
            data = model.model_validate(result).model_dump(by_alias=True, exclude_none=True)
            break
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
            if bad & required or not bad:
                problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:4])
                return None, f"the answer doesn't fit the sensor's fields — {problems}"
            for key in bad:
                result.pop(key, None)
    else:
        return None, "the answer doesn't fit the sensor's fields"
    if result.get("source"):
        data["source"] = result["source"]
    return data, ""


# ── Robust statistics: thresholds from the data, not constants ───────────────────────────────────────────────────

def center(values: list[float]) -> tuple[float, float]:
    """(median, robust spread): 1.4826 × the median absolute deviation (≈ σ for a normal spread, unmoved by
    outliers)."""
    median = statistics.median(values)
    return median, 1.4826 * statistics.median(abs(v - median) for v in values)


def relative_spread(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    median, spread = center(values)
    return spread / abs(median) if median else None


def outliers(values: list[float], k: float = 4.0, floor: float = 0.002) -> list[bool]:
    """Which values are off the others by more than k robust spreads (and at least `floor` of the median): one
    source that read another number (a previous close) stands out from many that agree."""
    median, spread = center(values)
    limit = max(k * spread, floor * abs(median), 1e-9)
    return [abs(v - median) > limit for v in values]


def tolerance(member: dict) -> float:
    """How far two sources of this group may differ and still agree: 4× the spread its sources usually show
    (measured each night), between 0.2 % and 5 %; 1 % until measured."""
    spread = member.get("spread")
    return AGREE if spread is None else min(max(4 * spread, 0.002), 0.05)


# ── Stored sources ───────────────────────────────────────────────────────────────────────────────────────────────

async def save(db: ArcadeDB, domain: str, code: str, need: str, author: str, sample: dict, *, name: str | None = None,
               group: str | None = None, description: str | None = None, key_fields: list[str] | None = None,
               url: str = "", schema: list[dict] | None = None) -> dict:
    """Store a tested module (replacing a sensor of the same name). `name` / `group`: a source joining a group
    (default: the module's NAME, a group of its own); `schema`: the group's fields, checked on every reading."""
    meta, problems = inspect_code(code, domain)
    if problems:
        raise SensorError("; ".join(problems))
    pages = meta.get("pages") or {}
    source = url or next(iter(pages.values()), "")
    doc = {"name": name or meta["name"], "group": group or name or meta["name"],
           "description": (description or meta["description"]).strip(), "params": meta["params"], "code": code,
           "need": need, "author": author, "status": "active", "sample": json.dumps(shrink(sample), default=str)[:3000],
           "tested_at": now(), "last_run_at": None, "last_ok": True, "kind": "page" if pages else "api",
           "host": urlsplit(source).netloc.lower().removeprefix("www.") if source else "", "url": source,
           "key_fields": key_fields or [], "schema": schema or [], "history": "1", "avg_seconds": None,
           "last_error": None, "repairs": 0, "refusals": 0, "cooldown_until": None}
    if existing := await get_sensor(db, domain, doc["name"]):
        await kgdb.update_doc(db, domain, "Sensor", existing["uid"], doc, SENSOR_JSON)
        return {**existing, **doc}
    return await kgdb.insert_doc(db, domain, "Sensor", doc, SENSOR_JSON)


async def _run_member(db: ArcadeDB, domain: str, member: dict, args: dict, schema: list[dict] | None = None,
                      doubted: str = "") -> dict:
    """One source, its answer checked against the group's fields, and its outcome recorded: success history,
    speed, last error, which parameters it failed for, and — when the site refused us — a rest that doubles with
    every refusal in a row (6 h … 48 h). `doubted`: the answer came back but the other sources outvoted it."""
    started = time.monotonic()
    out = await run_code(member["code"], args, domain)
    seconds = round(time.monotonic() - started, 2)
    if out["ok"] and schema is not None:
        data, problem = conform(out["result"], schema)
        out = {"ok": True, "result": data} if data is not None else {"ok": False, "error": problem}
    if doubted:
        out = {"ok": False, "error": doubted}
    avg = member.get("avg_seconds")
    misses = dict(member.get("misses") or {})
    key = _params_key(args)
    changes = {"last_run_at": now(), "last_ok": out["ok"],
               "history": ((member.get("history") or "") + "01"[out["ok"]])[-HISTORY:],
               "avg_seconds": seconds if avg is None else round(0.7 * avg + 0.3 * seconds, 2)}
    if out["ok"]:
        misses.pop(key, None)
        changes |= {"last_error": None, "refusals": 0, "cooldown_until": None}
    else:
        error = str(out.get("error"))[:300]
        misses[key] = now()
        changes["last_error"] = error
        if _BLOCKED.search(error):
            refusals = (member.get("refusals") or 0) + 1
            hours = min(COOLDOWN_HOURS * 2 ** (refusals - 1), 48)
            changes |= {"refusals": refusals, "cooldown_until": (datetime.now(timezone.utc) + timedelta(hours=hours))
                        .isoformat(timespec="seconds")}
            if "robots" in error.lower() and refusals >= 2:
                changes["status"] = "retired"   # the site said no twice, hours apart: it doesn't want to be read
    changes["misses"] = dict(sorted(misses.items(), key=lambda kv: kv[1])[-50:])
    await kgdb.update_doc(db, domain, "Sensor", member["uid"], changes, SENSOR_JSON)
    return out


def _site(host: str) -> str:
    """finance.yahoo.com / query1.finance.yahoo.com → yahoo.com (good enough to tell providers apart)."""
    return ".".join(host.split(".")[-2:])


def agree(a, b, tolerance: float = AGREE) -> bool:
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and abs(a - b) <= tolerance * max(abs(a), abs(b), 1e-9))


# The last value each group read per parameters: a reading far from it gets a second opinion.
_last: dict[tuple, tuple[float, float]] = {}
SURPRISE = 0.03          # a 3 % move since the last reading within SURPRISE_HOURS is checked against another source
SURPRISE_HOURS = 6


async def read(db: ArcadeDB, domain: str, name: str, params: dict | None = None,
               verify_field: str | None = None) -> dict:
    """Read a sensor now: its sources best first until one answers in the group's shape. Missing parameters take
    their example values. A second source is asked when `verify_field` is given (compared on it, "agreement") or
    when the main value moved surprisingly since the last reading; if those two disagree, a third settles it and the
    source that was outvoted is recorded as having failed."""
    from core.playbooks import value_at  # noqa: PLC0415 — playbooks imports this module

    sources = await members(db, domain, name)
    if not sources:
        raise SensorError(f"No active sensor {name!r}")
    spec = sources[0]["params"]
    unknown = set(params or {}) - set(spec)
    if unknown:
        raise SensorError(f"Unknown parameters {sorted(unknown)}; {name} takes {list(spec)}")
    args = {**{k: v.get("example") for k, v in spec.items()}, **(params or {})}
    schema = next((m["schema"] for m in sources if m.get("schema")), None) or schema_of(sources[0])
    keys = next((m["key_fields"] for m in sources if m.get("key_fields")), [])
    field = verify_field or (keys[0] if keys else None)
    tol = tolerance(sources[0])
    answers, failed = [], []
    queue = _route(sources, args)
    if not queue:
        return {"sensor": name, "params": args, "read_at": now(), "ok": False,
                "error": "every source of this sensor is resting after refusing automated reads (robots.txt)"}

    def wanted() -> int:
        """How many answers are needed: 1; 2 to verify or after a surprising move; 3 when those two disagree."""
        if not answers or not field:
            return 1
        first = value_at(answers[0][1].get("result"), field)
        if len(answers) >= 2:
            second = value_at(answers[1][1].get("result"), field)
            return 3 if isinstance(first, (int, float)) and not agree(first, second, tol) else 2
        if verify_field:
            return 2
        last = _last.get((domain, name, _params_key(args)))
        recent = last and time.monotonic() - last[1] < SURPRISE_HOURS * 3600
        return 2 if recent and isinstance(first, (int, float)) and not agree(first, last[0], SURPRISE) else 1

    while queue and len(answers) < wanted():
        member = queue.pop(0)
        out = await _run_member(db, domain, member, args, schema)
        if out["ok"]:
            answers.append((member, out))
            # A second opinion should come from another provider (sg.finance.yahoo.com confirming
            # finance.yahoo.com proves little): sources of other sites first.
            sites = {_site(host_of(m)) for m, _ in answers}
            queue.sort(key=lambda m: (_site(host_of(m)) in sites, cooling(m)))
        else:
            failed.append({"source": host_of(member) or member["name"], "error": str(out.get("error"))[:200]})
    if not answers:
        return {"sensor": name, "params": args, "read_at": now(), "ok": False,
                "error": "every source failed — " + "; ".join(f"{f['source']}: {f['error']}" for f in failed)[:1500]}

    chosen = answers[0]
    if field and len(answers) == 3:
        values = [value_at(a[1].get("result"), field) for a in answers]
        if all(isinstance(v, (int, float)) for v in values):
            odd = outliers(values, floor=tol)
            chosen = next(a for a, o in zip(answers, odd) if not o)
            for (m, _), o in zip(answers, odd):
                if o:   # outvoted: this source read something else this time
                    await _run_member(db, domain, m, args, schema,
                                      doubted=f"disagreed with the other sources on {field}")
    member, out = chosen
    reading = {"sensor": name, "source": host_of(member) or member["name"], "params": args, "read_at": now(), **out}
    if failed:
        reading["failed_sources"] = failed
    value = value_at(out.get("result"), field) if field else None
    if isinstance(value, (int, float)):
        _last[(domain, name, _params_key(args))] = (value, time.monotonic())
    if len(answers) > 1 and field:
        other = next(a for a in answers if a is not chosen)
        theirs = value_at(other[1].get("result"), field)
        reading["agreement"] = {"field": field, "value": value, "other_source": host_of(other[0]) or other[0]["name"],
                                "other_value": theirs, "agree": agree(value, theirs, tol),
                                "sources_read": len(answers)}
    return reading


async def remove(db: ArcadeDB, domain: str, name: str) -> None:
    """Remove one source — or, by its group name, the whole group."""
    targets = [s for s in await list_sensors(db, domain) if s["name"] == name] or \
        [s for s in await list_sensors(db, domain) if group_of(s) == name]
    if not targets:
        raise SensorError(f"No sensor {name!r}")
    for sensor in targets:
        await kgdb.delete_doc(db, domain, "Sensor", sensor["uid"])
