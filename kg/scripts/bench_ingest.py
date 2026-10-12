"""Ingest benchmark: the same pages through the whole pipeline, timed per stage, on a throwaway domain.

    uv run python scripts/bench_ingest.py run <label> [--pages data/bench/pages.json] [--concurrency N]
    uv run python scripts/bench_ingest.py compare <a.json> <b.json>

`run` copies a real domain's ontology (BENCH_LIKE, default ai_chips) into a temporary domain — the real domains are
never touched — ingests every page, records the timings, the counts and the facts it stored, and drops the domain.
`compare` measures how far two runs agree: the share of one run's facts that the other also found (by meaning:
embedding similarity ≥ MATCH) and the share of identical (source, relation, target) triples. Two runs of the same
engine show the noise floor; an engine is as accurate as the baseline when its agreement stays within it.
"""

import asyncio
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("SOVEREIGN_DOMAINS_DIR", tempfile.mkdtemp(prefix="bench-domains-"))
sys.path.insert(0, str(ROOT / "kg"))
os.chdir(ROOT / "kg")

from core import kg  # noqa: E402
from core import database as kgdb  # noqa: E402
from core.database import ArcadeDB  # noqa: E402
from core.embeddings import embed  # noqa: E402
from core.names import name_key  # noqa: E402
from domains import registry  # noqa: E402

RUNS = ROOT / "data" / "bench" / "runs"
LIKE = os.getenv("BENCH_LIKE", "ai_chips")
MATCH = 0.85


def _like() -> tuple[str, dict]:
    for base in (ROOT / "data" / "kg" / "domains", ROOT / "kg" / "domains"):
        if (base / LIKE / "ontology.json").exists():
            config = registry._config(base / LIKE)
            return config.get("description") or LIKE, json.loads((base / LIKE / "ontology.json").read_text())
    raise SystemExit(f"no ontology for {LIKE}")


async def run(label: str, pages_path: Path, concurrency: int) -> dict:
    description, ontology = _like()
    domain = registry.create_domain(f"bench {label}", description)
    registry.ontology_path(domain).write_text(json.dumps(ontology))
    db = ArcadeDB()
    await kgdb.ensure_domain_db(db, domain)
    pages = json.loads(pages_path.read_text())
    timings, counts, reports = Counter(), Counter(), []
    start = time.monotonic()
    try:
        sem = asyncio.Semaphore(concurrency)

        async def one(p):
            async with sem:
                t = time.monotonic()
                r = await kg.ingest(db, domain, p["markdown"], source=p["url"], title=p["title"])
                return p, r, time.monotonic() - t
        for p, r, secs in await asyncio.gather(*(one(p) for p in pages)):
            timings.update({k: v for k, v in r["timings"].items() if isinstance(v, (int, float))})
            counts.update({k: r[k] for k in ("entities_created", "facts_created", "facts_strengthened",
                                              "facts_invalidated", "facts_rejected", "review_items", "chunks")})
            reports.append({"title": p["title"], "seconds": round(secs, 1)})
        wall = time.monotonic() - start
        facts = await db.cypher(domain, "MATCH (a:Entity)-[r:Fact]->(b:Entity) RETURN a.name AS source, type(r) AS "
                                        "relation, b.name AS target, r.fact AS fact")
        result = {"label": label, "wall_seconds": round(wall, 1), "pages": reports, "timings": dict(timings),
                  "counts": dict(counts), "facts": facts, "concurrency": concurrency,
                  "model": os.getenv("LLM_MODEL"), "ollama": os.getenv("OLLAMA_BASE_URL")}
    finally:
        await kgdb.drop_domain_db(db, domain)
        registry.delete_domain(domain)
        await db.close()
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


async def compare(a_path: Path, b_path: Path) -> dict:
    a, b = json.loads(a_path.read_text()), json.loads(b_path.read_text())
    va = await embed([f["fact"] for f in a["facts"]]) if a["facts"] else []
    vb = await embed([f["fact"] for f in b["facts"]]) if b["facts"] else []

    def recall(xs, ys):
        if not xs:
            return 0.0
        return sum(1 for x in xs if ys and max(_cos(x, y) for y in ys) >= MATCH) / len(xs)

    def triples(run):
        return {(name_key(f["source"]), f["relation"], name_key(f["target"])) for f in run["facts"]}
    ta, tb = triples(a), triples(b)
    return {"a": a["label"], "b": b["label"], "facts": (len(a["facts"]), len(b["facts"])),
            "a_found_in_b": round(recall(va, vb), 3), "b_found_in_a": round(recall(vb, va), 3),
            "same_triples": round(len(ta & tb) / max(len(ta | tb), 1), 3),
            "seconds": (a["wall_seconds"], b["wall_seconds"])}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        args = sys.argv[2:]
        label = args[0]
        pages = Path(args[args.index("--pages") + 1]) if "--pages" in args else ROOT / "data" / "bench" / "pages.json"
        conc = int(args[args.index("--concurrency") + 1]) if "--concurrency" in args else 1
        r = asyncio.run(run(label, pages, conc))
        print(json.dumps({k: r[k] for k in ("label", "wall_seconds", "counts", "pages")}, ensure_ascii=False))
        print("stage seconds:", {k: round(v) for k, v in sorted(r["timings"].items(), key=lambda kv: -kv[1])})
    elif cmd == "compare":
        print(json.dumps(asyncio.run(compare(Path(sys.argv[2]), Path(sys.argv[3]))), ensure_ascii=False))
    else:
        print(__doc__)
