"""Source catalog — what the Scouts learned about where live data can be read, shared by every domain.

Entries are URL templates — public knowledge, nothing of a tenant's — with their record:
    url, kind, host, params (names), need (+ its embedding), tries, works, recent (last outcomes, "1"/"0"),
    last_ok, last_at, added_at
and refusals: hosts behind bot protection, and URL templates robots.txt keeps out — skipped by every scout for
REFUSAL_DAYS instead of being knocked on again.

It evolves by itself, with no list kept by hand:
    every scout       records each candidate's outcome — a template that works is added (or credited), one from the
                      catalog that fails is debited, a refusal is remembered
    the health check  credits or debits the template of every source it reads, night after night
    pruning           a template whose last three outcomes failed is dropped
    reuse             a scout of a new need starts with the templates of the most similar needs learned before
                      (cosine similarity of the need texts' embeddings ≥ SIMILAR, the same parameters — a single
                      parameter is renamed: {symbol} becomes {ticker}), best record first
"""

import asyncio
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import embeddings
from core.database import now

PATH = Path(__file__).resolve().parents[1] / ".scout-catalog.json"
SIMILAR = 0.72          # need texts this similar share templates
REFUSAL_DAYS = 7
_REFUSED = re.compile(r"^(robots\.txt|bot protection)")
_lock = asyncio.Lock()


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return {"entries": [], "refused": {}}
    if isinstance(data, list):   # the first format: a plain list of templates
        data = {"entries": data, "refused": {}}
    data.setdefault("refused", {})
    return data


def _save(data: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(PATH)


def _rate(e: dict) -> float:
    return (e.get("works", 0) + 1) / (e.get("tries", 0) + 2)


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def _vectors(texts: list[str]) -> list[list[float]] | None:
    try:
        return await embeddings.embed(texts)
    except Exception:
        return None


def _rename(url: str, old: str, new: str) -> str:
    return re.sub(r"{" + re.escape(old) + r"(\|\w+)?}", lambda m: "{" + new + (m[1] or "") + "}", url)


def _key(url: str, params: list[str]) -> tuple:
    return url, tuple(sorted(params))


def _refused(data: dict, c: dict) -> str | None:
    """Why a candidate is skipped (a refusal younger than REFUSAL_DAYS), or None."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REFUSAL_DAYS)).isoformat(timespec="seconds")
    for k in (f"url:{c['url']}", f"host:{c['host']}"):
        if (r := data["refused"].get(k)) and r["at"] > cutoff:
            return r["reason"]
    return None


async def suggest(need: str, params: list[str], skip: set[str]) -> list[dict]:
    """Candidates for a need from what similar needs taught: best record × similarity first, one per site."""
    async with _lock:
        data = _load()
        missing = [e for e in data["entries"] if not e.get("vector")]
        if missing and (vs := await _vectors([e["need"] for e in missing])):
            for e, v in zip(missing, vs):
                e["vector"] = v
            _save(data)
    target = await _vectors([need])
    ranked: dict[str, tuple[float, dict]] = {}
    for e in data["entries"]:
        if len(e["params"]) != len(params):
            continue
        same = sorted(e["params"]) == sorted(params)
        if not same and len(params) != 1:
            continue
        similarity = _cos(target[0], e["vector"]) if target and e.get("vector") else 0.0
        if similarity < SIMILAR or e["host"] in skip:
            continue
        url = e["url"] if same else _rename(e["url"], e["params"][0], params[0])
        candidate = {"host": e["host"], "kind": e["kind"], "url": url, "outcome": "queued", "from": "catalog"}
        if _refused(data, candidate):
            continue
        score = similarity * _rate(e)
        if e["host"] not in ranked or score > ranked[e["host"]][0]:
            ranked[e["host"]] = (score, candidate)
    return [c for _, c in sorted(ranked.values(), key=lambda x: -x[0])]


def refused(c: dict) -> str | None:
    """Why the catalog says to skip this candidate now, or None."""
    return _refused(_load(), c)


def _credit(e: dict, ok: bool) -> None:
    e["tries"] = e.get("tries", 0) + 1
    e["works"] = e.get("works", 0) + ok
    e["recent"] = (e.get("recent", "") + "01"[ok])[-3:]
    e.update(last_ok=ok, last_at=now())


def _prune(data: dict) -> None:
    data["entries"] = [e for e in data["entries"] if e.get("recent") != "000"]


async def record(need: str, params: list[str], candidates: list[dict]) -> dict:
    """After a scout: learn from every candidate's outcome. Returns what changed."""
    async with _lock:
        data = _load()
        index = {_key(e["url"], e["params"]): e for e in data["entries"]}
        vector = None
        changed = {"added": 0, "credited": 0, "debited": 0, "refusals": 0}
        for c in candidates:
            outcome = c.get("outcome", "")
            if _REFUSED.match(outcome):
                key = f"host:{c['host']}" if outcome.startswith("bot") else f"url:{c['url']}"
                data["refused"][key] = {"reason": outcome[:80], "at": now()}
                changed["refusals"] += 1
            e = index.get(_key(c["url"], params))
            if outcome == "works":
                if not e:
                    if vector is None:
                        vector = (await _vectors([need]) or [None])[0]
                    e = {"url": c["url"], "kind": c["kind"], "host": c["host"], "params": sorted(params),
                         "need": need, "vector": vector, "added_at": now()}
                    data["entries"].append(e)
                    index[_key(c["url"], params)] = e
                    changed["added"] += 1
                else:
                    changed["credited"] += 1
                _credit(e, True)
            elif e and outcome not in ("queued", "reachable") and not outcome.startswith("reachable ("):
                _credit(e, False)
                changed["debited"] += 1
        _prune(data)
        _save(data)
        return changed


async def record_source(url: str, params: list[str], ok: bool) -> None:
    """A stored source read by the health check: its template's record follows it."""
    if not url:
        return
    async with _lock:
        data = _load()
        for e in data["entries"]:
            if _key(e["url"], e["params"]) == _key(url, params):
                _credit(e, ok)
        _prune(data)
        _save(data)


def stats() -> dict:
    data = _load()
    return {"templates": len(data["entries"]), "refusals": len(data["refused"]),
            "needs": len({e["need"] for e in data["entries"]})}
