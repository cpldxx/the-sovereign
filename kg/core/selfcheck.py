"""Security self-check — the invariants that must hold, verified on the running system every night.

    static     sensor code that imports os / evals / reads files / reaches the real builtins is refused before it runs
    webhooks   a webhook to this machine or a private network is refused
    sandbox    sensor code can't reach this machine's services (the KG API, ArcadeDB, Ollama) — by name or by a
               private address (Docker Desktop's host gateway)
    robots     the sandbox refuses a URL robots.txt keeps bots out of
    auth       the KG API answers nothing without credentials
    scope      a connection limited to one domain can't reach another

A check that can't run (Docker down, no internet) is "inconclusive", not a failure. Any failure becomes an alert in
every domain — someone has to look.
"""

import httpx

from core import actions, auth, sensors
from core.auth import Principal
from core.database import ArcadeDB
from domains.registry import list_domains

_PROBE_HOST = '''import httpx
NAME = "selfcheck"
DESCRIPTION = "x"
PARAMS = {"url": {"type": "string", "description": "u", "example": "x"}}
def run(url: str) -> dict:
    return {"status": httpx.get(url, timeout=5).status_code}
'''
# Each address separately: a refusal of one must not hide another being reached.
_PROBE_MANY = '''import httpx
NAME = "selfcheck"
DESCRIPTION = "x"
PARAMS = {"urls": {"type": "string", "description": "u", "example": "x"}}
def run(urls: str) -> dict:
    out = {}
    for url in urls.split(","):
        try:
            out[url] = "reached: HTTP " + str(httpx.get(url, timeout=5).status_code)
        except Exception as e:
            out[url] = str(e)
    return out
'''
# This machine from inside the container: its own loopback (host.docker.internal is pinned to it), the cloud metadata
# address, and private addresses that reach the host on Docker Desktop (its gateway, the default bridge).
SANDBOX_TARGETS = ("http://127.0.0.1:8080/health", "http://localhost:2480/", "http://host.docker.internal:11434/",
                   "http://169.254.169.254/", "http://192.168.65.254:11434/", "http://172.17.0.1:11434/",
                   "http://10.0.0.1/")
_REFUSED = "only public internet addresses"   # the egress guard's own refusal — a connection error proves nothing
# Ways past the static check seen before (2026-10-14): the real builtins by name, a file opener as an attribute.
_BYPASSES = ('import os', 'x = __builtins__["__import__"]("socket")', 'x = io.open("/etc/passwd")',
             'x = httpx._utils', 'x = __import__("os")')


async def _static() -> tuple[str, str]:
    for line in _BYPASSES:
        _, problems = sensors.inspect_code(_PROBE_HOST.replace("import httpx", f"import httpx\nimport io\n{line}"))
        if not problems:
            return "fail", f"a module with {line!r} was accepted"
    return "pass", ""


async def _webhooks() -> tuple[str, str]:
    for url in ("http://127.0.0.1:11434/api/tags", "http://169.254.169.254/latest", "http://10.0.0.1/"):
        try:
            await actions._check_destination(url)
        except actions.ActionError:
            continue
        if actions.ALLOW_PRIVATE_WEBHOOKS:
            return "pass", "private webhooks allowed by ALLOW_PRIVATE_WEBHOOKS (single-user install)"
        return "fail", f"a webhook to {url} was accepted"
    return "pass", ""


async def _sandbox() -> tuple[str, str]:
    try:
        out = await sensors.run_code(_PROBE_MANY, {"urls": ",".join(SANDBOX_TARGETS)})
    except Exception as e:
        return "inconclusive", f"sandbox not runnable ({type(e).__name__})"
    if not out["ok"]:
        return "inconclusive", str(out.get("error"))[:150]
    return sandbox_verdict(out["result"])


def sandbox_verdict(result: dict) -> tuple[str, str]:
    """Pass only when the guard itself refused every address: a connection error (nothing listening there today)
    would hide an address the sandbox can in fact reach."""
    reached = [u for u, why in result.items() if str(why).startswith("reached")]
    if reached:
        return "fail", f"sensor code reached {', '.join(reached)} from the sandbox"
    other = [f"{u}: {str(why)[:60]}" for u, why in result.items() if _REFUSED not in str(why)]
    return ("pass", "") if not other else ("fail", "not refused by the egress guard — " + "; ".join(other))[:300]


async def _robots() -> tuple[str, str]:
    out = await sensors.probe("https://query1.finance.yahoo.com/v8/finance/chart/NVDA")
    if out["ok"]:
        return "fail", "the sandbox read a URL robots.txt disallows"
    return ("pass", "") if "robots.txt" in str(out.get("error")) else \
        ("inconclusive", str(out.get("error"))[:150])


async def _auth() -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{auth.KG_URL}/domains")
    except httpx.HTTPError as e:
        return "inconclusive", f"KG API unreachable ({type(e).__name__})"
    return ("pass", "") if r.status_code == 401 else ("fail", f"/domains without credentials answered {r.status_code}")


async def _scope() -> tuple[str, str]:
    scoped = Principal("service", scope="a")
    ok = auth.denial(scoped, "b") and not auth.denial(scoped, "a", "owner") and scoped.visible(["a", "b"]) == ["a"]
    return ("pass", "") if ok else ("fail", "a domain-scoped connection reached another domain")


CHECKS = {"static": _static, "webhooks": _webhooks, "sandbox": _sandbox, "robots": _robots, "auth": _auth,
          "scope": _scope}


async def run(db: ArcadeDB, alert: bool = True) -> dict:
    results = {}
    for name, check in CHECKS.items():
        try:
            status, detail = await check()
        except Exception as e:
            status, detail = "inconclusive", f"{type(e).__name__}: {e}"[:150]
        results[name] = {"status": status, "detail": detail}
    failed = {k: v for k, v in results.items() if v["status"] == "fail"}
    if failed and alert:
        message = "; ".join(f"{k}: {v['detail']}" for k, v in failed.items())
        for domain in list_domains():
            try:
                await actions.propose(db, domain, "alert", {
                    "title": "Security self-check failed", "message": message, "severity": "critical"},
                    "The nightly security self-check found a broken safeguard.", source="selfcheck")
            except Exception as e:
                print(f"[selfcheck] alert for {domain} failed: {type(e).__name__}", flush=True)
    return {"ok": not failed, "checks": results}
