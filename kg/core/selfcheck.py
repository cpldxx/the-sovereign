"""Security self-check — the invariants that must hold, verified on the running system every night.

    robots     the research crawler refuses a URL robots.txt keeps bots out of
    auth       the KG API answers nothing without credentials
    scope      a connection limited to one domain can't reach another

A check that can't run (the research service down, no internet) is "inconclusive", not a failure. Any failure is
left in the Head's inbox in every domain — someone has to look.
"""

import os

import httpx

from core import auth
from core.auth import Principal
from core.database import ArcadeDB
from domains.registry import list_domains

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
DISALLOWED = "https://query1.finance.yahoo.com/v8/finance/chart/NVDA"   # robots.txt: Disallow for every bot


async def _robots() -> tuple[str, str]:
    try:
        async with httpx.AsyncClient(timeout=20, headers=auth.headers()) as client:
            r = await client.get(f"{RESEARCH_URL}/robots", params={"url": DISALLOWED})
        r.raise_for_status()
    except httpx.HTTPError as e:
        return "inconclusive", f"research service unreachable ({type(e).__name__})"
    body = r.json()
    if body.get("allowed"):
        return "fail", "the crawler would read a URL robots.txt disallows"
    return ("pass", "") if "robots" in (body.get("reason") or "") else ("inconclusive", str(body.get("reason"))[:150])


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


CHECKS = {"robots": _robots, "auth": _auth, "scope": _scope}


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
        from core import crew   # crew sits above this module
        message = "Security self-check failed: " + "; ".join(f"{k}: {v['detail']}" for k, v in failed.items())
        for domain in list_domains():
            try:
                await crew.notify(db, domain, "learning", message, {"selfcheck": results})
            except Exception as e:
                print(f"[selfcheck] notice for {domain} failed: {type(e).__name__}", flush=True)
    return {"ok": not failed, "checks": results}
