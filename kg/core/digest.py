"""Daily digest — refresh what went stale, then report what changed.

repair             name keys that leaked onto the wrong entity are removed
refresh_summaries  entities whose facts changed get their summary rewritten from their current facts
build_report       the period's changes (from the graph) + research runs (from the research service)
                   → Reporter → a stored Report: a briefing to read and a short version to speak

The research service's nightly loop calls both after its runs (POST /domains/{d}/reports); the UI and
the Head Agent (daily_report tool) read the stored reports.
"""

import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx

from agents.reporter import write_briefing
from agents.summarizer import summarize
from core import auth
from core import database as kgdb
from core.database import ArcadeDB
from core.tracing import llm_usage, observe
from domains.registry import load_domain

RESEARCH_URL = os.getenv("SOVEREIGN_RESEARCH_URL", "http://localhost:8070").rstrip("/")
SUMMARY_BATCH = 8
SUMMARY_LIMIT = int(os.getenv("SUMMARY_REFRESH_LIMIT", "150"))  # entities per run, most changed first

_running: set[str] = set()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def running(domain: str) -> bool:
    return domain in _running


async def refresh_summaries(db: ArcadeDB, domain: str, since: str) -> dict:
    """Rewrite the summaries of entities whose facts changed since `since`."""
    uids = await kgdb.changed_entities(db, domain, since, SUMMARY_LIMIT)
    refreshed = 0
    with observe("refresh_summaries", input={"entities": len(uids)}):
        for i in range(0, len(uids), SUMMARY_BATCH):
            details = [d for u in uids[i:i + SUMMARY_BATCH] if (d := await kgdb.entity_detail(db, domain, u))]
            items = [{**d["entity"], "facts": [f for f in d["facts"] if f["valid"]]} for d in details]
            items = [it for it in items if it["facts"]]
            for j, summary in (await summarize(items)).items():
                await kgdb.set_entity_summary(db, domain, items[j]["uid"], summary)
                refreshed += 1
    return {"candidates": len(uids), "refreshed": refreshed}


async def _research_runs(domain: str, since: str) -> list[dict] | None:
    """Research jobs created or finished in the period; None when the research service is unreachable."""
    try:
        async with httpx.AsyncClient(base_url=RESEARCH_URL, timeout=10, headers=auth.headers(domain)) as client:
            jobs = (await client.get("/jobs", params={"domain": domain, "limit": 100})).json()["jobs"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return [
        {k: j.get(k) for k in ("id", "mode", "question", "status", "error", "created_at", "finished_at", "summary")}
        for j in jobs if j["created_at"] >= since or (j.get("finished_at") or "") >= since
    ]


def _ontology_gaps(changes: dict) -> list[dict]:
    """Types and relations the period's sources needed but the ontology lacks, most needed first."""
    gaps: Counter[str] = Counter()
    for ep in changes["episodes"]:
        gaps.update(ep.get("ontology_gaps") or {})
    return [{"kind": k.split(":", 1)[0], "name": k.split(":", 1)[1], "count": n} for k, n in gaps.most_common(10)]


def _digest_text(domain: str, description: str, start: str, end: str, totals: dict, changes: dict,
                 runs: list[dict] | None, gaps: list[dict]) -> str:
    lines = [f"DOMAIN: {domain} — {description}", f"PERIOD: {start} → {end} (UTC)",
             f"GRAPH NOW: {totals['entity_count']} entities, {totals['fact_count']} valid facts, "
             f"{totals['invalid_fact_count']} superseded, {totals['episode_count']} sources, "
             f"{totals['pending_reviews']} pending reviews", ""]

    def section(title: str, items: list, fmt, cap: int) -> None:
        lines.append(f"{title} ({len(items)}{f', showing {cap}' if len(items) > cap else ''}):")
        lines.extend(f"- {fmt(x)}" for x in items[:cap])
        lines.append("")

    if runs is None:
        lines += ["RESEARCH RUNS: research service unreachable", ""]
    else:
        def run(j):
            s = j.get("summary") or {}
            what = f"{j['mode']}{': ' + j['question'] if j.get('question') else ''}"
            if j["status"] != "done":
                return f"{what} — {j['status']}{': ' + j['error'] if j.get('error') else ''}"
            return (f"{what} — read {s.get('pages_read', 0)} pages ({s.get('pages_failed', 0)} unreadable), "
                    f"+{s.get('entities_created', 0)} entities, +{s.get('facts_created', 0)} facts")
        section("RESEARCH RUNS", runs, run, 20)
    section("NEW SOURCES", changes["episodes"],
            lambda e: f"{e['title'] or e['source']} — {e['source']}"
                      f"{' (partial: paywalled or truncated)' if e.get('content_status') == 'partial' else ''}", 30)
    section("NEW ENTITIES (most mentioned first)", changes["entities"],
            lambda e: f"{e['name']} ({e['type']}): {e.get('summary') or ''}", 30)
    section("NEW FACTS (strongest first)", changes["facts_created"],
            lambda f: f"({f['weight']:.2f}) {f['fact']}", 40)
    section("STRENGTHENED FACTS (confirmed by another source)", changes["facts_strengthened"],
            lambda f: f"({f['weight']:.2f}, {f['evidence']} sources) {f['fact']}", 20)
    section("SUPERSEDED FACTS (no longer true)", changes["facts_invalidated"],
            lambda f: f"WAS: {f['fact']} — {f.get('invalid_reason') or ''}", 20)
    section("REVIEW ITEMS OPENED", changes["reviews_opened"], lambda r: f"[{r['kind']}] {r['summary']}", 15)
    section("REVIEW DECISIONS", changes["reviews_decided"],
            lambda r: f"[{r['status']}] {r['summary']} → {r.get('resolution') or ''}", 15)
    section("ONTOLOGY GAPS (items dropped because the ontology has no such type / relation)", gaps,
            lambda g: f"{g['kind']} '{g['name']}': {g['count']} items", 10)
    return "\n".join(lines)


def _plain_briefing(stats: dict) -> tuple[str, str, str]:
    """Fallback when nothing changed or the Reporter fails: the numbers, no prose."""
    c = stats["changes"]
    if not any(c.values()) and not stats["research"]["runs"]:
        line = f"No new knowledge in the last {stats['hours']} hours."
        return line, line, line
    headline = (f"{c['sources']} new sources: +{c['entities_created']} entities, +{c['facts_created']} facts, "
                f"{c['facts_strengthened']} strengthened, {c['facts_invalidated']} superseded.")
    research = stats["research"]
    briefing = (f"## What changed\n- {headline}\n\n## Needs attention\n"
                f"- {research['failed']} of {research['runs']} research runs failed\n"
                f"- {stats['graph']['pending_reviews']} review items pending")
    return headline, briefing, headline


async def build_report(db: ArcadeDB, domain: str, hours: int = 24, refresh: bool = True) -> dict:
    """Refresh stale summaries, then write and store the report for the last `hours`. Raises ValueError for
    unknown domains and RuntimeError when a report for the domain is already being generated."""
    dom = load_domain(domain)
    if domain in _running:
        raise RuntimeError(f"A report for {domain} is already being generated")
    _running.add(domain)
    started = time.monotonic()
    try:
        with observe("daily_report", input={"hours": hours}, metadata={"domain": domain},
                     tags=[domain, "report"]) as obs:
            end_dt = datetime.now(timezone.utc)
            start, end = _iso(end_dt - timedelta(hours=hours)), _iso(end_dt)
            repaired = await kgdb.repair_name_keys(db, domain)  # names that leaked onto other entities
            summaries = await refresh_summaries(db, domain, start) if refresh else {"candidates": 0, "refreshed": 0}
            changes = await kgdb.changes_since(db, domain, start)
            runs = await _research_runs(domain, start)
            totals = await kgdb.domain_stats(db, domain)
            gaps = _ontology_gaps(changes)
            done = [j for j in runs or [] if j["status"] == "done"]
            stats = {
                "hours": hours,
                "changes": {
                    "sources": len(changes["episodes"]),
                    "entities_created": len(changes["entities"]),
                    "facts_created": len(changes["facts_created"]),
                    "facts_strengthened": len(changes["facts_strengthened"]),
                    "facts_invalidated": len(changes["facts_invalidated"]),
                    "reviews_opened": len(changes["reviews_opened"]),
                    "reviews_decided": len(changes["reviews_decided"]),
                },
                "research": {
                    "reachable": runs is not None,
                    "runs": len(runs or []),
                    "failed": sum(1 for j in runs or [] if j["status"] == "failed"),
                    "pages_read": sum((j.get("summary") or {}).get("pages_read", 0) for j in done),
                    "pages_failed": sum((j.get("summary") or {}).get("pages_failed", 0) for j in done),
                    "seconds": {k: sum(((j.get("summary") or {}).get("seconds") or {}).get(k, 0) for j in done)
                                for k in ("research", "ontology", "ingest")},
                },
                "summaries": summaries,
                "names_repaired": repaired,
                "ontology_gaps": gaps,
                # LLM calls / tokens per activity across ALL domains (one local model serves them all), or None
                "llm": await llm_usage(start, end),
                "graph": {k: totals[k] for k in ("entity_count", "fact_count", "invalid_fact_count",
                                                 "episode_count", "pending_reviews")},
            }
            if any(stats["changes"].values()) or runs:
                description = dom["config"].get("description") or domain
                try:
                    b = await write_briefing(_digest_text(domain, description, start, end, totals, changes, runs, gaps))
                    headline, briefing, spoken = b.headline.strip(), b.briefing.strip(), b.spoken.strip()
                except Exception as e:
                    print(f"[Reporter] failed: {type(e).__name__}: {e}", flush=True)
                    headline, briefing, spoken = _plain_briefing(stats)
            else:
                headline, briefing, spoken = _plain_briefing(stats)
            stats["seconds"] = round(time.monotonic() - started)

            def fact(f: dict) -> dict:
                return {k: f.get(k) for k in ("uid", "relation", "fact", "source_uid", "source_name", "target_uid",
                                              "target_name", "weight", "evidence", "invalid_reason")}
            report = {
                "kind": "daily", "period_start": start, "period_end": end,
                "headline": headline, "briefing": briefing, "spoken": spoken, "stats": stats,
                "digest": {
                    "sources": changes["episodes"],
                    "entities": [{k: e[k] for k in ("uid", "name", "type")} for e in changes["entities"][:60]],
                    "facts_created": [fact(f) for f in changes["facts_created"][:60]],
                    "facts_strengthened": [fact(f) for f in changes["facts_strengthened"][:40]],
                    "facts_invalidated": [fact(f) for f in changes["facts_invalidated"][:40]],
                    "reviews_decided": changes["reviews_decided"][:40],
                    "research": runs or [],
                },
            }
            uid = await kgdb.save_report(db, domain, report)
            if obs:
                obs.update(output={"headline": headline, "stats": stats})
            return await kgdb.get_report(db, domain, uid)
    finally:
        _running.discard(domain)
