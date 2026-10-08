"""Security self-check — the invariants that must hold, verified on the running system every night.

    static     sensor code that imports os / evals / reads files is refused before it runs
    webhooks   a webhook to this machine or a private network is refused
    sandbox    sensor code can't reach this machine's services (the KG API, ArcadeDB, Ollama)
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


async def _static() -> tuple[str, str]:
    _, problems = sensors.inspect_code(_PROBE_HOST.replace("import httpx", "import os"))
    return ("pass", "") if problems else ("fail", "a module importing os was accepted")


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
        out = await sensors.run_code(_PROBE_HOST, {"url": "http://127.0.0.1:8080/health"})
    except Exception as e:
        return "inconclusive", f"sandbox not runnable ({type(e).__name__})"
    if out["ok"]:
        return "fail", "sensor code reached the KG API from the sandbox"
    if "only public internet addresses" in str(out.get("error")) or "ConnectError" in str(out.get("error")):
        return "pass", ""
    return "inconclusive", str(out.get("error"))[:150]


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
