"""Scout — as many sources as can be found for one live-data need; every one that works joins the sensor's group.

One source is often blocked (robots.txt, bot protection) or changes its layout; with many, enough always work. Measured
for a stock price (2026-10-07): 11 of 25 candidate sites readable under Sovereign's rules — and the first number on a
page was the wrong one on 3 of those 11 (the day's high, a chart axis, an article), hence a parser per source and a
cross-check between them.

    1 plan       the need → the group's shape (name, parameters, fields; key fields are what sources are compared on).
                 Expanding an existing sensor keeps its shape.
    2 collect    real URLs from web search hits that show the example values, made into templates (…/quote/{symbol}),
                 plus the Scout's own list of 20-30 APIs and pages; while fewer than SCOUT_MAX_SOURCES answer, up to
                 ROUNDS rounds, each asking for sites not tried yet (guessed URLs are mostly 404s and landing pages)
                 Round one starts with the catalog (core/catalog.py): templates that worked for similar needs before,
                 in any domain; sites that refused us lately are skipped
    (reach)      when the group already reads a value, a page that doesn't show it (within the group's tolerance, at
                 least 1 %) is dropped before any parser is written
    3 reach      each candidate with the example parameters: pages opened in a browser (robots.txt, no bot-protection
                 workarounds), APIs checked against robots.txt and probed from the sandbox — blocked ones are recorded
    4 parse      for the reachable ones (APIs first, up to SCOUT_MAX_SOURCES): a parser written from what the source
                 actually returned, tested in the sandbox on the example AND a second value (a parser fitted to the
                 NVDA page broke on AMD), fixed once
    5 agree      the main key's values across every working source (and the group's current ones) must sit within 4
                 robust spreads (median absolute deviation) of their median — a source that read another number (a
                 previous close) stands out; spread-out values (counts, headlines) aren't voted on. Thresholds come
                 from the data: fixed ones let a previous close pass (3 %) or rejected every good source (10 %)
    6 keep       every source left joins the group; reading it falls back from one to the next

The nightly health check reads every source once; a group with fewer than MIN_WORKING working sources is scouted again
(at most every RESCOUT_DAYS).
"""

import asyncio
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from agents import scout as agent
from core import auth, catalog, playbooks, sensors
from core import database as kgdb
from core.database import ArcadeDB, now

MAX_CANDIDATES = 30   # per round
ROUNDS = 3            # rounds of candidates while fewer than SCOUT_MAX_SOURCES answer
MAX_SOURCES = int(os.getenv("SCOUT_MAX_SOURCES", "12"))   # parsers written per run (each is one or two LLM calls)
MIN_WORKING = 2
RESCOUT_DAYS = 7
DEAD_RESCOUT_HOURS = 6   # a sensor with no working source is searched again after this long
SAMPLE_CHARS = 9000


def _slug(text: str, limit: int = 41) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    slug = slug if slug[:1].isalpha() else f"s_{slug}"
    return slug[:limit].rstrip("_")


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _shape_text(name: str, description: str, params: dict, fields: list[dict]) -> str:
    lines = [f"name: {name}", f"description: {description}", "params:"]
    lines += [f"  - {k} ({v['type']}): {v.get('description', '')} — example {v.get('example')!r}" for k, v in params.items()]
    lines.append("fields:")
    lines += [f"  - {f['name']}{' (number)' if f['numeric'] else ''}"
              f"{' KEY, required' if f['key'] else ' required' if f.get('required') else ' (optional)'}: {f['description']}"
              for f in fields]
    return "\n".join(lines)


def _existing_shape(member: dict) -> str:
    sample = sensors.sample_of(member) or {}
    fields = ", ".join(f"{k} ({type(v).__name__})" for k, v in (sample if isinstance(sample, dict) else {}).items())
    return (f"name: {sensors.group_of(member)}\ndescription: {member['description']}\n"
            f"params: {json.dumps(member['params'])}\noutput fields (with types, from a reading): {fields}")


def _check(result, fields: list[dict]) -> str:
    """The problem with a module's output, or ""."""
    if not isinstance(result, dict):
        return "run() must return a dict"
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    missing = [f["name"] for f in fields if (f["key"] or f.get("required"))
               and (not number(result.get(f["name"])) if f["numeric"] else result.get(f["name"]) in (None, "", [], {}))]
    if missing:
        return f"required fields missing, empty or not numbers: {missing} (got keys {list(result)[:20]})"
    wrong = [f["name"] for f in fields if f["numeric"] and result.get(f["name"]) is not None
             and not number(result[f["name"]])]
    return f"fields must be numbers, not strings: {wrong}" if wrong else ""


def _template(url: str, args: dict) -> str | None:
    """A real URL that shows the example values → a URL template ("…/quote/NVDA" → "…/quote/{symbol}"); None when a
    value isn't in it. Lower- or upper-case occurrences become {name|lower} / {name|upper}."""
    for name, value in args.items():
        value = str(value)
        if len(value) < 2:
            return None
        found = False
        for variant, mark in ((value, ""), (value.lower(), "|lower"), (value.upper(), "|upper")):
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])")
            if pattern.search(url):
                url, found = pattern.sub(f"{{{name}{mark}}}", url), True
                break
        if not found:
            return None
    return url


async def _search(need: str, args: dict, args2: dict | None, domain: str) -> tuple[str, list[dict]]:
    """Web search hits for the Scout's prompt, and page candidates made from the hits' real URLs (a guessed URL
    template is the commonest failure: 404s and landing pages)."""
    values = " ".join(str(v) for v in args.values())
    queries = [f"{values} {need}", f"{values} live", f"{values} today", f"{need} free API"]
    if args2:
        queries.append(f"{' '.join(str(v) for v in args2.values())} {need}")
    hits: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=60, headers=auth.headers(domain)) as client:
        for q in queries:
            try:
                r = await client.get(f"{sensors.RESEARCH_URL}/search", params={"q": q.strip(), "limit": 10})
                for h in r.json().get("results", []):
                    hits.setdefault(h.get("url", ""), h.get("title", ""))
            except (httpx.HTTPError, ValueError):
                continue
    pages, hosts = [], set()
    for url in hits:
        template = _template(url, args) or (_template(url, args2) if args2 else None)
        if template and (host := _host(url)) not in hosts:
            hosts.add(host)
            pages.append({"host": host, "kind": "page", "url": template, "outcome": "queued", "from": "search"})
    text = "\n".join(f"- {title} — {url}" for url, title in list(hits.items())[:25] if url)
    return text, pages


def _shows(text: str, reference: float | None, tolerance: float = 0.01) -> bool:
    """Does the text contain a number within 1 % of the value other sources read? (No reference: assume yes.) 3 %
    let 404 and overview pages through: some number on them was always that close."""
    if reference is None:
        return True
    for raw in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            if abs(float(raw.replace(",", "")) - reference) <= tolerance * abs(reference):
                return True
        except ValueError:
            continue
    return False


async def _reach(c: dict, args: dict, domain: str, reference: float | None = None, tolerance: float = 0.01) -> None:
    """Open one candidate with the example parameters; record the outcome (and a sample when it answered)."""
    url = sensors.fill(c["url"], args)
    started = time.monotonic()
    if c["kind"] == "page":
        page = await sensors.render(url, domain)
        if page.get("error"):
            err = page["error"]
            c["outcome"] = ("robots.txt" if "robots" in err else "bot protection" if re.search(r"anti-bot|challenge|captcha", err, re.I)
                            else f"unreadable: {err[:80]}")
        elif len(page.get("text", "")) < 200:
            c["outcome"] = "empty page"
        elif not _shows(page["text"], reference, tolerance):
            c["outcome"] = f"doesn't show the value (~{reference}): wrong page?"
        else:
            c.update(outcome="reachable", sample=page["text"][:SAMPLE_CHARS])
    elif not await sensors.robots_allows(url, domain):
        c["outcome"] = "robots.txt"
    else:
        out = await sensors.probe(url)
        res = out.get("result") or {}
        status = res.get("status")
        if not out["ok"]:
            c["outcome"] = f"unreachable: {str(out.get('error'))[:80]}"
        elif status in (401, 402, 403):
            c["outcome"] = f"needs a key / refused ({status})"
        elif status == 429:
            c["outcome"] = "rate limited (429)"
        elif status != 200 or not res.get("body", "").strip():
            c["outcome"] = f"HTTP {status}"
        elif not _shows(res["body"], reference, tolerance):
            c["outcome"] = f"doesn't show the value (~{reference})"
        else:
            c.update(outcome="reachable", sample=f"content-type: {res.get('content_type')}\n{res['body'][:SAMPLE_CHARS]}")
    c["seconds"] = round(time.monotonic() - started, 1)


async def _parse(c: dict, shape: str, fields: list[dict], args: dict, domain: str, log: list[str],
                 args2: dict | None = None) -> None:
    """Write and test a parser for a reachable candidate (one fix with the test's error). With `args2` (other example
    values) it must work for those too — a parser fitted to one page (one ticker) breaks on the next."""
    sample = c.pop("sample", "")
    problem = code = ""
    for _ in range(2):
        try:
            answer = await agent.parser(shape, c["kind"], c["url"], sample, problem, code)
        except Exception as e:
            c["outcome"] = f"parser failed: {type(e).__name__}"
            return
        if not answer.code:
            c["outcome"] = f"no data on it: {answer.note[:80]}"
            return
        code = answer.code
        meta, problems = sensors.inspect_code(code, domain)
        if c["kind"] == "page" and (not meta.get("pages") or "page(" not in code or re.search(r"^\s*import httpx", code, re.M)):
            problems.append("a page source must declare PAGES and read page(\"main\") — the page is already opened in a "
                            "browser for you; don't fetch it with httpx")
        out = {"ok": False, "error": "; ".join(problems)} if problems else await sensors.run_code(code, args, domain)
        problem = _check(out.get("result"), fields) if out["ok"] else str(out.get("error"))[:600]
        if not problem and args2:
            out2 = await sensors.run_code(code, args2, domain)
            if problem := (_check(out2.get("result"), fields) if out2["ok"] else str(out2.get("error"))[:400]):
                problem = (f"works for {args} but not for {args2}: {problem} — don't depend on text that only "
                           f"appears for one value (a company name, a fixed number)")
            elif (key := next((f["name"] for f in fields if f["key"]), None)) and \
                    out2["result"].get(key) == out["result"].get(key) not in (None, "", [], {}):
                problem = (f"returns the same {key} ({out['result'].get(key)}) for {args} and {args2}: the URL or the "
                           f"parsing ignores the parameter")
        if not problem:
            c.update(outcome="works", code=code, result=out["result"],
                     value={f["name"]: _brief(out["result"].get(f["name"])) for f in fields if f["key"]})
            log.append(f"works: {c['host']} → {c['value']}")
            return
    c["outcome"] = f"parser failed: {problem[:120]}"
    log.append(f"failed: {c['host']} ({problem[:80]})")


def _brief(v):
    """A key value as shown in the job: numbers as they are, lists by their length."""
    if isinstance(v, (int, float)):
        return v
    return f"{len(v)} items" if isinstance(v, (list, dict)) else str(v)[:60]


def _agreement(works: list[dict], current: list[dict], keys: list[str], others: list[str] = ()) -> None:
    """Drop the sources that read another number than the rest — decided from the data, not a fixed tolerance:
    the main key's values (3+) must sit within 4 robust spreads of their median (at least 0.2 %); when they are
    spread out by nature (> 5 %: a count of results, headlines) there is nothing to vote on. The group's other numbers
    are voted the same way only when they are tight too; otherwise just for the opposite sign, both clearly off zero
    (a Morningstar parser gave +0.9 for a -0.92 % day; after hours, pages differ on which session's change they show:
    -0.30 … -0.74 %). Earlier fixed tolerances let a previous close pass (3 %) or rejected every good source (10 %)."""
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    for field, strict in [(k, True) for k in keys[:1]] + [(f, False) for f in others]:
        voters = [c for c in works if c["outcome"] == "works" and number(c["result"].get(field))]
        values = [c["result"][field] for c in voters] + [r[field] for r in current if number(r.get(field))]
        if len(values) < 3:
            continue
        spread = sensors.relative_spread(values)
        tight = spread is not None and spread <= 0.05
        odd = sensors.outliers(values, k=4.0 if strict else 6.0, floor=0.002 if strict else 0.01) if tight else \
            [False] * len(values)
        median = statistics.median(values)
        for c, o in zip(voters, odd):
            v = c["result"][field]
            flipped = not strict and abs(v) > 0.2 and abs(median) > 0.2 and (v > 0) != (median > 0)
            if o or flipped:
                c["outcome"] = f"disagrees: {field} {v} vs {median} elsewhere"


async def discover(db: ArcadeDB, j: dict, description: str) -> None:
    """A scout job (core/coding.py): the sources found join group j["group"] (new or existing)."""
    domain, need, log = j["domain"], j["need"], j["log"]
    existing = await sensors.members(db, domain, j["group"]) if j.get("group") else []
    j["status"] = "planning"
    plan = await agent.plan(need, f"{domain} — {description}", _existing_shape(existing[0]) if existing else "")
    if existing:
        name, params = sensors.group_of(existing[0]), existing[0]["params"]
        sample = sensors.sample_of(existing[0])
        keep = set(sample) if isinstance(sample, dict) else set()
        plan.fields = [f for f in plan.fields if f.name in keep] or plan.fields
        # Fields live triggers compare must be in every source.
        watched = {t["field"] for pb in await playbooks.list_playbooks(db, domain)
                   if (t := pb.get("trigger")) and t.get("sensor") == name}
    else:
        watched = set()
        name = _slug(plan.name)
        params = {p.name: {"type": p.type, "description": p.description, "example": p.example} for p in plan.params}
        if await sensors.members(db, domain, name):
            name = _slug(f"{name}_{int(time.time()) % 10000}")
    # A parameter echoed back is not what the need is about.
    fields = [{**f.model_dump(), "required": f.name in watched} for f in plan.fields if f.name not in params]
    if fields and not any(f["key"] for f in fields):
        next((f for f in fields if f["numeric"]), fields[0])["key"] = True
    # Few optional fields: a parser asked for many tends to fail on the one a page doesn't show.
    optional = [f for f in fields if not f["key"] and not f["required"] and f["numeric"]][:3]
    fields = [f for f in fields if f["key"] or f["required"] or f in optional]
    keys = [f["name"] for f in fields if f["key"]]
    j["group"] = name
    shape = _shape_text(name, existing[0]["description"] if existing else plan.description, params, fields)
    args = {k: v.get("example") for k, v in params.items()}
    others = {p.name: p.other_example for p in plan.params}
    args2 = {k: others.get(k) for k in params} if all(others.get(k) not in (None, "", args[k]) for k in params) else None
    log.append(f"shape: {name}({', '.join(params)}) → key {keys}; tested on {args}" + (f" and {args2}" if args2 else ""))

    # What the group's current sources read now: the reference a candidate page must show, and votes later.
    current = []
    for m in existing:
        out = await sensors.run_code(m["code"], args, domain)
        if out["ok"] and isinstance(out["result"], dict):
            current.append(out["result"])
    refs = [r[keys[0]] for r in current if keys and isinstance(r.get(keys[0]), (int, float))]
    reference = statistics.median(refs) if refs else None

    j["status"] = "collecting"
    hits, from_search = await _search(need, args, args2, domain)
    tried = {sensors.host_of(m) for m in existing} - {""}
    from_catalog = [] if j.get("repair") else await catalog.suggest(need, list(params), tried)
    log.append(f"catalog: {len(from_catalog)} templates from similar needs ({catalog.stats()['templates']} known)")
    cands: list[dict] = []
    j["candidates"] = cands
    sem = asyncio.Semaphore(4)

    async def reach(c):
        if why := catalog.refused(c):   # it refused us lately: don't knock again
            c["outcome"] = f"skipped: refused within {catalog.REFUSAL_DAYS} days ({why})"
            return
        async with sem:
            await _reach(c, args, domain, reference, max(0.01, sensors.tolerance(existing[0])) if existing else 0.01)

    # Repair: the broken sources' own pages, read again and parsed anew — no new candidates.
    broken = [m for m in existing if m["name"] in (j.get("repair") or [])]
    if broken:
        j["status"] = "probing"
        for m in broken:
            cands.append({"host": sensors.host_of(m), "kind": m.get("kind") or "page", "url": sensors.url_of(m),
                          "outcome": "queued", "repair": m["name"]})
        await asyncio.gather(*(reach(c) for c in cands))
        log.append(f"repair: {len(cands)} source(s), {sum(c['outcome'] == 'reachable' for c in cands)} reachable")
    # Rounds until enough sources answer: each asks for sites not tried yet (most famous ones refuse robots).
    for round_ in range(0 if broken else ROUNDS):
        j["status"] = "collecting"
        try:
            found = await agent.candidates(need, shape, hits, sorted(tried))
        except Exception as e:
            log.append(f"round {round_ + 1}: no candidates ({type(e).__name__})")
            break
        new = []
        # A listed URL with the example value written in (…/quotes/NVDA) would read NVDA for every symbol.
        listed = [{"host": _host(x.url), "kind": x.kind, "outcome": "queued",
                   "url": x.url if sensors._PLACEHOLDER.search(x.url) else (_template(x.url, args) or x.url)}
                  for x in found]
        for c in ((from_catalog + from_search) if round_ == 0 else []) + listed:
            if c["host"] and c["host"] not in tried and urlsplit(c["url"]).scheme in ("http", "https"):
                tried.add(c["host"])
                new.append(c)
        if not new:
            break
        cands += new[:MAX_CANDIDATES]
        j["status"] = "probing"
        await asyncio.gather(*(reach(c) for c in new[:MAX_CANDIDATES]))
        reachable = [c for c in cands if c["outcome"] == "reachable"]
        log.append(f"round {round_ + 1}: {len(new)} candidates, {len(reachable)} reachable so far")
        if len(reachable) >= MAX_SOURCES:
            break
    reachable = [c for c in cands if c["outcome"] == "reachable"]

    j["status"] = "coding"
    # Templates that worked before first, then APIs (fast), then by speed.
    reachable.sort(key=lambda c: (c.get("from") != "catalog", c["kind"] != "api", c["seconds"]))
    for c in reachable[MAX_SOURCES:]:
        c["outcome"] = "reachable (not tried: SCOUT_MAX_SOURCES)"
    for c in reachable[:MAX_SOURCES]:
        await _parse(c, shape, fields, args, domain, log, args2)

    j["status"] = "testing"
    _agreement(cands, current, [k for k in keys if any(f["name"] == k and f["numeric"] for f in fields)],
               others=[f["name"] for f in fields if f["numeric"] and not f["key"]])

    # The group's fields as every source must answer them (checked with pydantic on every reading).
    sample = next((c["result"] for c in cands if c["outcome"] == "works"), {})
    schema = [{"name": f["name"], "required": bool(f["key"] or f.get("required")),
               "type": "number" if f["numeric"] else sensors._kind_of(sample.get(f["name"], ""))} for f in fields]
    kept = []
    for c in cands:
        c.pop("sample", None)
        if c["outcome"] != "works":
            continue
        member = c.get("repair") or _slug(f"{name}_{c['host']}")
        try:
            await sensors.save(db, domain, c["code"], need, "scout", c["result"], name=member, group=name,
                               description=existing[0]["description"] if existing else plan.description,
                               key_fields=keys, url=c["url"], schema=schema)
            kept.append(member)
        except sensors.SensorError as e:
            c["outcome"] = f"not saved: {e}"[:120]
        for k in ("code", "result"):
            c.pop(k, None)
    for m in await sensors.members(db, domain, name):
        await kgdb.update_doc(db, domain, "Sensor", m["uid"], {"scouted_at": now()})
    learned = await catalog.record(need, list(params), cands)
    log.append(f"catalog: {learned}")
    j.update(sensor=name if kept or existing else None, sources=len(kept))
    j["note"] = (f"{len(kept)} new source(s) of {len(cands)} candidates for {name}"
                 + (f" (it had {len(existing)})" if existing else ""))
    if not kept and not existing:
        raise sensors.SensorError(f"no candidate source worked ({len(reachable)} of {len(cands)} reachable)")


async def health_check(db: ArcadeDB, domain: str) -> dict:
    """Read every source of every group once (example parameters); scout again where fewer than MIN_WORKING work."""
    from core import coding  # noqa: PLC0415 — coding runs scout jobs

    report = {}
    for group in await sensors.groups(db, domain):
        sources = await sensors.members(db, domain, group["name"])
        args = {k: v.get("example") for k, v in group["params"].items()}
        schema = next((m["schema"] for m in sources if m.get("schema")), None) or sensors.schema_of(sources[0])
        key = next((m["key_fields"][0] for m in sources if m.get("key_fields")), None)
        working = resting = 0
        values = []
        for m in sources:
            if sensors.cooling(m):   # it refused us: don't knock again before its rest is over
                resting += 1
                continue
            out = await sensors._run_member(db, domain, m, args, schema)
            working += out["ok"]
            await catalog.record_source(m.get("url") or "", list(group["params"]), out["ok"])
            if out["ok"] and key and isinstance(v := (out["result"] or {}).get(key), (int, float)):
                values.append(v)
        report[group["name"]] = {"working": working, "sources": len(sources), "resting": resting}
        # How far this group's sources usually differ: the tolerance its readings are compared with.
        if (spread := sensors.relative_spread(values)) is not None:
            report[group["name"]]["spread"] = round(spread, 5)
            for m in sources:
                await kgdb.update_doc(db, domain, "Sensor", m["uid"], {"spread": round(spread, 6)})
        last = max((m.get("scouted_at") or "" for m in sources), default="")
        # A dead sensor is searched again soon (a search that found nothing mustn't park it for a week).
        wait = timedelta(hours=DEAD_RESCOUT_HOURS) if working == 0 else timedelta(days=RESCOUT_DAYS)
        stale = last < (datetime.now(timezone.utc) - wait).isoformat(timespec="seconds")
        busy = any(r.get("group") == group["name"] and r["status"] not in ("done", "failed")
                   for r in coding.jobs(domain))
        if working < MIN_WORKING and group.get("need") and stale and not busy:
            report[group["name"]]["rescout"] = coding.submit(db, domain, group["need"], "scout", group=group["name"])["id"]
            busy = True
        # A source that worked and now fails twice in a row for another reason than a refusal: its site changed.
        # Its page is read again and a new parser written (the old one stays until a new one passes); one that
        # can't be repaired twice is retired.
        fresh = {m["uid"]: m for m in await sensors.members(db, domain, group["name"], with_code=False)}
        broken = []
        for m in sources:
            m = {**m, **fresh.get(m["uid"], {})}
            history, error = m.get("history") or "", m.get("last_error") or ""
            if not (history.endswith("00") and "1" in history and not sensors.cooling(m)
                    and not sensors._BLOCKED.search(error) and "disagreed" not in error and sensors.url_of(m)):
                continue
            if (m.get("repairs") or 0) >= 2:
                await kgdb.update_doc(db, domain, "Sensor", m["uid"], {"status": "retired"})
                report[group["name"]].setdefault("retired", []).append(m["name"])
            else:
                await kgdb.update_doc(db, domain, "Sensor", m["uid"], {"repairs": (m.get("repairs") or 0) + 1})
                broken.append(m["name"])
        if broken and group.get("need") and not busy:
            report[group["name"]]["repair"] = coding.submit(db, domain, group["need"], "scout", group=group["name"],
                                                            repair=broken)["id"]
    return report
