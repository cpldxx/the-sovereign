"""The synthesis (core/synthesis.py): a review written from the graph's numbered facts, holding only points that cite
them, its key sources counted (papers first) not chosen, and rewritten only when its facts change. The model and
Graph RAG are stood in; db tests use a throwaway database (conftest.scratch)."""

import pytest

from core import charter, synthesis
from core.synthesis import Review

CHARTER = {"goal": "Know which treatments slow decline", "depth": "expert",
           "questions": ["Which drugs slow Alzheimer's decline?", "What do blood tests show?"]}
FACTS = [
    {"uid": "f1", "fact": "Lecanemab slowed decline by 27% over 18 months.", "weight": 0.9, "valid": True,
     "source_uid": "e1", "target_uid": "e1", "sources": ["ep_paper", "ep_blog"]},
    {"uid": "f2", "fact": "Lecanemab was approved in 2023.", "weight": 0.8, "valid": True,
     "source_uid": "e1", "target_uid": "e2", "sources": ["ep_fda"]},
]
EPISODES = [{"uid": "ep_paper", "source": "https://doi.org/10.1056/x", "title": "NEJM", "tier": "paper"},
            {"uid": "ep_blog", "source": "https://medium.com/x", "title": "A blog", "tier": "blog"},
            {"uid": "ep_fda", "source": "https://www.fda.gov/x", "title": "FDA", "tier": "official"}]
EV = {"facts": FACTS, "superseded": [{"uid": "s1", "fact": "Lecanemab showed no benefit.", "sources": ["ep_blog"]}],
      "episodes": {e["uid"]: e for e in EPISODES}, "mark": "m"}


def test_a_point_that_cites_no_fact_is_dropped():
    points = synthesis._points(["Lecanemab slows decline [1].", "Everyone agrees it works.",
                                "It once looked useless [S1][9].", "A blog said so (S1), in 2023 (2023)."], EV)
    assert points == [{"text": "Lecanemab slows decline.", "facts": ["f1"]},
                      {"text": "It once looked useless.", "facts": ["s1"]},        # [9] doesn't exist
                      {"text": "A blog said so, in 2023 (2023).", "facts": ["s1"]}]  # (S1) is a citation, (2023) not


def test_the_key_sources_are_counted_papers_and_official_first():
    ranked = synthesis._key_sources({"f1", "f2", "s1"}, EV)
    assert [(s["tier"], s["facts"]) for s in ranked] == [("paper", 1), ("official", 1), ("blog", 2)]


class _Agent:
    reviews: list[Review] = []
    calls = 0

    def __init__(self, *a, system_prompt, **kw):
        self.system = system_prompt
        self.checks = []

    def output_validator(self, fn):
        self.checks.append(fn)
        return fn

    async def run(self, prompt):
        _Agent.calls += 1
        for r in _Agent.reviews:
            try:
                for check in self.checks:
                    r = check(r)
                return type("Run", (), {"output": r})
            except synthesis.ModelRetry:
                continue
        raise synthesis.UnexpectedModelBehavior("no review passed")


@pytest.fixture
def domain(scratch, monkeypatch):
    monkeypatch.setattr(charter.registry, "load_domain", lambda d: {"config": {"description": "Alzheimer's"}})
    monkeypatch.setattr(synthesis, "Agent", _Agent)
    graph = {"facts": list(FACTS)}

    async def query(db, d, question, k):
        return {"facts": graph["facts"] if "drugs" in question else [], "entities": [], "episodes": []}

    async def get_episodes(db, d, uids):
        return [e for e in EPISODES if e["uid"] in uids]
    monkeypatch.setattr(synthesis.kg, "query", query)
    monkeypatch.setattr(synthesis.kgdb, "get_episodes", get_episodes)
    _Agent.calls = 0
    _Agent.reviews = [Review(summary="Lecanemab slows decline by 27% [1], approved in 2023 [2].",
                             consensus=["It slows decline, by a paper [1].", "It works for everyone."],
                             debates=[], open=["Does it help late stages?"])]
    return scratch, graph


@pytest.mark.db
async def test_a_page_per_core_question_rewritten_only_when_its_facts_change(domain):
    (db, d), graph = domain
    with pytest.raises(synthesis.SynthesisError):
        await synthesis.refresh(db, d)                     # no charter, no review
    await charter.save(db, d, CHARTER)
    assert {k: v for k, v in (await synthesis.refresh(db, d)).items() if k != "seconds"} == {"written": 2, "unchanged": 0}
    first, blood = await synthesis.pages(db, d)
    assert first["summary"] == "Lecanemab slows decline by 27%, approved in 2023." and first["summary_facts"] == ["f1", "f2"]
    assert first["consensus"] == [{"text": "It slows decline, by a paper.", "facts": ["f1"]}]   # the uncited one went
    assert [s["tier"] for s in first["sources"]] == ["paper", "official", "blog"]
    assert blood["summary"] == "" and blood["open"] == ["What do blood tests show?"]      # nothing known: no model
    assert _Agent.calls == 1
    assert (await synthesis.refresh(db, d))["unchanged"] == 2 and _Agent.calls == 1        # nothing changed
    graph["facts"] = [{**FACTS[0], "weight": 0.95}, FACTS[1]]                              # confirmed again
    assert (await synthesis.refresh(db, d))["written"] == 1 and _Agent.calls == 2
    assert (await synthesis.refresh(db, d, force=True))["written"] == 2
    await charter.save(db, d, {"questions": ["Which drugs slow Alzheimer's decline?"]})
    assert [p["question"] for p in await synthesis.pages(db, d)] == ["Which drugs slow Alzheimer's decline?"]


@pytest.mark.db
async def test_a_review_is_written_again_until_its_summary_cites(domain, monkeypatch):
    (db, d), _ = domain
    await charter.save(db, d, {**CHARTER, "questions": ["Which drugs slow Alzheimer's decline?"]})

    async def query(db_, d_, question, k):
        return {"facts": FACTS}
    monkeypatch.setattr(synthesis.kg, "query", query)
    _Agent.reviews = [Review(summary="Lecanemab slows decline."),                         # cites nothing
                      Review(summary="Lecanemab slows decline by 27% [1].")]
    await synthesis.refresh(db, d)
    [page] = await synthesis.pages(db, d)
    assert page["summary"] == "Lecanemab slows decline by 27%." and page["summary_facts"] == ["f1"]
    _Agent.reviews = [Review(summary="Lecanemab slows decline.",                          # never passes:
                             consensus=["It slows decline [1].", "It works for everyone."])]   # taken unchecked
    await synthesis.refresh(db, d, force=True)
    [page] = await synthesis.pages(db, d)
    assert page["summary_facts"] == []
    assert [p["text"] for p in page["consensus"]] == ["It slows decline."]              # only what cites stays


@pytest.mark.db
async def test_what_changed_is_the_superseded_facts_not_the_forgotten_ones(scratch):
    from core import database as kgdb
    db, d = scratch
    for uid, name in (("e1", "Lecanemab"), ("e2", "FDA")):
        await kgdb.create_entity(db, d, {"uid": uid, "name": name, "type": "drug", "keys": [name.lower()]}, [0.1, 0.2, 0.3])
    old = await kgdb.create_fact(db, d, "e1", "has_state", "e1", "Lecanemab showed no benefit.", None, "ep1", 0.7)
    weak = await kgdb.create_fact(db, d, "e1", "approved_by", "e2", "Approved in 2019.", None, "ep2", 0.4)
    await kgdb.create_fact(db, d, "e1", "has_state", "e1", "Lecanemab slowed decline by 27%.", None, "ep3", 0.9)
    await kgdb.invalidate_fact(db, d, "has_state", old, "a newer trial reports a 27% slowing")
    await kgdb.invalidate_fact(db, d, "approved_by", weak, "forgotten: a single weak source, not confirmed in 30 days")
    rows = await synthesis._superseded(db, d, ["e1"])
    assert [(r["fact"], r["reason"]) for r in rows] == [("Lecanemab showed no benefit.", "a newer trial reports a 27% slowing")]
