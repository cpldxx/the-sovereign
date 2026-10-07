"""Coder (built-in) — writes a sensor for a need: finds a free public data source, probes it, writes the module,
tests it in the sandbox and fixes it until it works.

Its tools only reach the outside world through the research service's rules (search; page reading with
robots.txt) and the sandbox (raw endpoint probes and test runs) — never from this process.
"""

import json
import os
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, RunContext, UsageLimits

from core import auth, sensors
from core.llm import model_for, model_settings

MODEL = model_for("coder")
RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
REQUEST_LIMIT = int(os.getenv("CODER_REQUEST_LIMIT", "30"))  # model turns per sensor


class SensorCode(BaseModel):
    code: str | None = Field(default=None, description="The final, tested module — null if no working source exists")
    note: str = Field(default="", description="The source used, or why no working sensor could be written")

    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


SYSTEM = """You are the Sovereign's Coder. You write SENSORS: small Python modules that read live data for a need.

How to work:
1. Find a public data source for the need: a documented API or feed (JSON, CSV, RSS/Atom) meant for programmatic
   use — an official one when an available API key is for it, otherwise a free keyless one. Use web_search and read_webpage to find it and read its documentation. Do not scrape pages
   whose robots.txt forbids it, and never work around a login, paywall, CAPTCHA or rate limit.
2. Probe the endpoint with http_get to see exactly what it returns.
3. Write the module and run test_sensor. Fix it until the test passes AND the output really answers the need.
4. Return the final tested code. If no free source works, return code = null and explain in note.
Never invent endpoints or field names: only use what you saw in documentation or in a probe."""


@dataclass
class Deps:
    domain: str
    log: list[str] = field(default_factory=list)  # shared with the request, so progress shows while it works
    last_ok_code: str | None = None


_agent = Agent(MODEL, name="coder", model_settings=model_settings(), system_prompt=SYSTEM, output_type=SensorCode,
               deps_type=Deps, retries=3)


@_agent.system_prompt
def _contract(ctx: RunContext[Deps]) -> str:
    """The module contract — with the API keys this domain's sensors may use."""
    return sensors.contract(ctx.deps.domain)


@_agent.tool
async def web_search(ctx: RunContext[Deps], query: str) -> str:
    """Search the web. Returns titles, URLs and snippets."""
    ctx.deps.log.append(f"search: {query}")
    try:
        async with httpx.AsyncClient(timeout=60, headers=auth.headers()) as client:
            r = await client.get(f"{RESEARCH_URL}/search", params={"q": query, "limit": 8})
        return json.dumps(r.json().get("results", []), ensure_ascii=False)
    except httpx.HTTPError as e:
        return f"search unavailable ({type(e).__name__})"


@_agent.tool
async def read_webpage(ctx: RunContext[Deps], url: str) -> str:
    """Read a web page (e.g. API documentation) as text. Respects robots.txt."""
    ctx.deps.log.append(f"read: {url}")
    try:
        async with httpx.AsyncClient(timeout=90, headers=auth.headers()) as client:
            r = await client.post(f"{RESEARCH_URL}/fetch", json={"url": url, "max_chars": 6000})
        page = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return f"could not read {url} ({type(e).__name__})"
    if page.get("status") == "failed":
        return f"could not read {url}: {page.get('error')}"
    return page.get("markdown", "")


_PROBE = '''import httpx
NAME = "probe"
DESCRIPTION = "probe"
PARAMS = {"url": {"type": "string", "description": "url", "example": ""}}

def run(url: str) -> dict:
    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
    return {"status": r.status_code, "content_type": r.headers.get("content-type", ""), "body": r.text[:2500]}
'''


@_agent.tool
async def http_get(ctx: RunContext[Deps], url: str) -> str:
    """GET a URL from the sandbox and show the raw response (status, content type, first 2500 characters)."""
    ctx.deps.log.append(f"probe: {url}")
    out = await sensors.run_code(_PROBE, {"url": url})
    return json.dumps(out.get("result") if out["ok"] else out, ensure_ascii=False)


@_agent.tool
async def test_sensor(ctx: RunContext[Deps], code: str) -> str:
    """Check the module against the rules and run it in the sandbox with its PARAMS examples."""
    meta, problems = sensors.inspect_code(code, ctx.deps.domain)
    if problems:
        ctx.deps.log.append(f"test: rejected ({'; '.join(problems)[:200]})")
        return "Rule problems: " + "; ".join(problems)
    out = await sensors.run_code(code, sensors.examples(meta), ctx.deps.domain)
    ctx.deps.log.append(f"test: {'ok' if out['ok'] else out['error'][:200]}")
    if out["ok"]:
        ctx.deps.last_ok_code = code
        return "Passed. Output: " + sensors.clip(out["result"])[:3000]
    return f"Failed: {out['error']}\n{out.get('trace', '')[-800:]}"


async def write_sensor(need: str, domain: str, description: str, log: list[str]) -> SensorCode:
    """The Coder's answer; its steps are appended to `log` as they happen. Never raises."""
    deps = Deps(domain=domain, log=log)
    prompt = f"Domain: {domain} — {description}\nNEED: {need}"
    try:
        out = (await _agent.run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=REQUEST_LIMIT))).output
    except Exception as e:
        deps.log.append(f"stopped: {type(e).__name__}: {str(e)[:200]}")
        # A run cut short (turn limit) may still have produced a module that passed its test.
        out = SensorCode(code=deps.last_ok_code, note=f"stopped early ({type(e).__name__})")
    return out
