"""Ingest that fails is read again, a page read twice is one source, decisions on vanished entities wait, names are
found as words, a query's sources follow its facts, and a state is compared only with states like it. Stand-in
agents; db tests use a throwaway domain."""

import secrets
from datetime import datetime, timedelta, timezone

import pytest

from agents.extractor import ExtractedEntity, ExtractedFact, Extraction
from agents.linker import Link
from agents.validator import FactVerdict
from core import database as kgdb
from core import kg
from domains import registry

TEXT = "Acme Labs enables the Widget Engine. Weak Gizmo is related to Acme Labs. " * 3


def _extraction():
    return Extraction(
        entities=[ExtractedEntity(name="Acme Labs", type="entity", description="a lab"),
                  ExtractedEntity(name="Widget Engine", type="tool", description="an engine"),
                  ExtractedEntity(name="Weak Gizmo", type="tool", description="a gizmo")],
        facts=[ExtractedFact(source="Acme Labs", relation="enables", target="Widget Engine",
                             fact="Acme Labs enables the Widget Engine."),
               ExtractedFact(source="Weak Gizmo", relation="related_to", target="Acme Labs",
                             fact="Weak Gizmo is related to Acme Labs.")])


@pytest.fixture
async def domain(db, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    name = registry.create_domain(f"Ingest fix {secrets.token_hex(3)}", "A throwaway domain for ingest fixes")
    await kgdb.ensure_domain_db(db, name, create=True)

    async def extract(text, *a, **k):
        return _extraction()

    async def judge(text, source, facts):
        return [FactVerdict(index=i, supported=True, reliability=0.9 if "enables" in f else 0.4)
                for i, f in enumerate(facts)]

    async def embed(texts):
        return [None] * len(texts)

    async def resolve(items):
        return []

    async def link(items):
        return []
    for fn in (extract, judge, embed, resolve, link):
        monkeypatch.setattr(kg, fn.__name__, fn)
    yield db, name
    await kgdb.drop_domain_db(db, name)


# ── Pure ───────────────────────────────────────────────────────────────────

def test_a_short_latin_name_is_found_only_as_a_word():
    assert not kg._in_text(["AI"], "The CEO said demand was strong.")
    assert not kg._in_text(["Arm"], "A pharmaceutical company announced results.")
    assert kg._in_text(["Arm"], "Arm Holdings licenses its designs.")
    assert kg._in_text(["GPU"], "It ships more GPUs this year.")                 # plural
    assert kg._in_text(["Nvidia"], "Nvidia's revenue doubled.")                  # possessive
    assert kg._in_text(["CoWoS-L"], "Capacity for CoWoS-L rose.")
    assert not kg._in_text(["CoWoS"], "Capacity for CoWoSx rose.")
    assert kg._in_text(["삼성전자"], "삼성전자는 3분기 실적을 발표했다.")              # no spaces between words
    assert kg._in_text(["SK하이닉스"], "SK하이닉스는 부진했다.")
    assert not kg._in_text(["SK"], "ASK하이닉스는 부진했다.")


def test_which_existing_episodes_are_read_again():
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert not kg._retry({"uid": "ep_1", "status": None, "created_at": old})        # legacy: done
    assert not kg._retry({"uid": "ep_1", "status": "done", "created_at": old})
    assert kg._retry({"uid": "ep_1", "status": "failed", "created_at": fresh})
    assert not kg._retry({"uid": "ep_1", "status": "pending", "created_at": fresh})  # someone is reading it
    assert kg._retry({"uid": "ep_1", "status": "pending", "created_at": old})        # its reader is gone
    kg._active.add("ep_1")
    try:
        assert not kg._retry({"uid": "ep_1", "status": "failed", "created_at": old})  # being read again right now
    finally:
        kg._active.discard("ep_1")


async def test_a_querys_sources_follow_its_facts(monkeypatch):
    facts = [{"uid": f"f_{i}", "source_uid": "e_a", "target_uid": "e_b", "sources": [f"ep_{9 - i}", "ep_shared"],
              "similarity": 0.9 - i / 10} for i in range(5)]

    async def embed(texts):
        return [[0.0]]

    async def none(*a, **k):
        return []

    async def search_facts(db, domain, vector, k):
        return facts

    async def get_episodes(db, domain, uids):
        return [{"uid": u} for u in sorted(uids)]          # the database's order is not the facts' order
    monkeypatch.setattr(kg, "embed", embed)
    for fn, stand_in in (("search_entities", none), ("expand", none), ("get_entities", none),
                         ("search_facts", search_facts), ("get_episodes", get_episodes)):
        monkeypatch.setattr(kgdb, fn, stand_in)
    out = await kg._query(None, "d", "q", 5, {"entities": [], "facts": [], "episodes": []})
    assert [e["uid"] for e in out["episodes"]] == ["ep_9", "ep_shared", "ep_8", "ep_7", "ep_6", "ep_5"]


async def test_a_question_in_another_language_is_also_searched_in_english(monkeypatch):
    """The graph is kept in English: a Korean question found facts about HBM4 in general, its English twin the answer."""
    by_vector = {"ko": [{"uid": "f_general", "similarity": 0.52, "source_uid": "e", "target_uid": "e"},
                        {"uid": "f_answer", "similarity": 0.40, "source_uid": "e", "target_uid": "e"}],
                 "en": [{"uid": "f_answer", "similarity": 0.77, "source_uid": "e", "target_uid": "e"}]}
    translated = []

    class Translator:
        def __init__(self, *a, **k):
            pass

        async def run(self, question):
            translated.append(question)
            return type("Run", (), {"output": "Which bonding replaces bumps in HBM4?"})

    async def embed(texts):
        return [["en"] if t.isascii() else ["ko"] for t in texts]

    async def search_facts(db, domain, vector, k):
        return by_vector[vector[0]]

    async def none(*a, **k):
        return []
    monkeypatch.setattr(kg, "Agent", Translator)
    monkeypatch.setattr(kg, "embed", embed)
    for fn, stand_in in (("search_entities", none), ("expand", none), ("get_entities", none),
                         ("search_facts", search_facts), ("get_episodes", none)):
        monkeypatch.setattr(kgdb, fn, stand_in)
    empty = {"entities": [], "facts": [], "episodes": []}
    out = await kg._query(None, "d", "HBM4에서 범프를 대체하는 결합 방식은?", 5, empty)
    assert [(f["uid"], f["similarity"]) for f in out["facts"]] == [("f_answer", 0.77), ("f_general", 0.52)]
    await kg._query(None, "d", "HBM4에서 범프를 대체하는 결합 방식은?", 5, empty)
    await kg._query(None, "d", "Which bonding replaces bumps?", 5, empty)
    assert len(translated) == 1                    # asked again: remembered — and an English question isn't translated


# ── Failed reads are read again ────────────────────────────────────────────

async def _episode(db, domain, uid):
    rows = await db.sql(domain, "SELECT status, error FROM Episode WHERE uid = :u", u=uid)
    return rows[0]


@pytest.mark.db
async def test_a_failed_extraction_is_read_again(domain, monkeypatch):
    db, name = domain
    good = kg.extract

    async def broken(*a, **k):
        return None
    monkeypatch.setattr(kg, "extract", broken)
    first = await kg.ingest(db, name, TEXT, source="https://example.com/a")
    assert first["failed"] and "Extractor" in first["reason"] and first["facts_created"] == 0
    assert (await _episode(db, name, first["episode_uid"]))["status"] == "failed"
    assert await kg.known_sources(db, name, ["https://example.com/a"]) == []   # research fetches it again

    monkeypatch.setattr(kg, "extract", good)
    second = await kg.ingest(db, name, TEXT, source="https://example.com/a")
    assert not second["duplicate"] and not second["failed"]
    assert second["episode_uid"] == first["episode_uid"] and second["facts_created"] == 1
    assert (await _episode(db, name, first["episode_uid"]))["status"] == "done"
    assert await kg.known_sources(db, name, ["https://example.com/a", "https://example.com/b"]) == [
        "https://example.com/a"]
    third = await kg.ingest(db, name, TEXT, source="https://example.com/a")
    assert third["duplicate"]


@pytest.mark.db
async def test_a_validator_failure_or_a_write_error_fails_the_episode(domain, monkeypatch):
    db, name = domain
    good = kg.judge

    async def no_verdicts(*a):
        return None
    monkeypatch.setattr(kg, "judge", no_verdicts)
    r = await kg.ingest(db, name, TEXT, source="t")
    assert r["failed"] and "Validator" in r["reason"]
    assert await db.cypher(name, "MATCH (e:Entity) RETURN e.uid AS uid") == []        # nothing half-written

    monkeypatch.setattr(kg, "judge", good)

    async def db_down(*a, **k):
        raise kgdb.ArcadeDBError("500 boom")
    monkeypatch.setattr(kgdb, "link_mentions", db_down)
    r = await kg.ingest(db, name, "Another text. " + TEXT, source="t")
    assert r["failed"] and "boom" in r["reason"]
    assert (await _episode(db, name, r["episode_uid"]))["status"] == "failed"


@pytest.mark.db
async def test_a_recent_pending_episode_is_not_read_twice(domain):
    db, name = domain
    first = await kg.ingest(db, name, TEXT, source="t")
    await kgdb.set_episode_status(db, name, first["episode_uid"], "pending")
    again = await kg.ingest(db, name, TEXT, source="t")
    assert again["duplicate"] and again.get("pending")


# ── One page, one source ───────────────────────────────────────────────────

@pytest.mark.db
async def test_a_page_read_again_does_not_add_evidence(domain, monkeypatch):
    db, name = domain
    url = "https://example.com/news"
    await kg.ingest(db, name, TEXT, source=url)
    [fact] = await db.cypher(name, "MATCH ()-[r:enables]->() RETURN r.uid AS uid, r.evidence AS evidence")

    async def link(items):
        return [Link(index=i, same_as=fact["uid"], confident=True) for i, _ in enumerate(items)]
    monkeypatch.setattr(kg, "link", link)
    changed = await kg.ingest(db, name, "Updated. " + TEXT, source=url)              # the page changed a little
    assert [f["status"] for f in changed["facts"] if f["relation"] == "enables"] == ["known"]
    other = await kg.ingest(db, name, "Elsewhere. " + TEXT, source="https://other.example/x")
    assert [f["status"] for f in other["facts"] if f["relation"] == "enables"] == ["strengthened"]
    [after] = await db.cypher(name, "MATCH ()-[r:enables]->() RETURN r.evidence AS evidence")
    assert after["evidence"] == 2                                                    # the first page + the other site


# ── Vanished entities, concurrent decisions ────────────────────────────────

@pytest.mark.db
async def test_approving_a_fact_whose_entity_is_gone_keeps_the_review(domain):
    db, name = domain
    await kg.ingest(db, name, TEXT, source="t")
    review = (await kgdb.list_reviews(db, name))[0]
    await db.cypher(name, "MATCH (e:Entity {uid: $u}) DETACH DELETE e", u=review["payload"]["source_uid"])
    with pytest.raises(ValueError, match="no longer exists"):
        await kg.resolve_review(db, name, review["uid"], approve=True)
    assert (await kgdb.get_review(db, name, review["uid"]))["status"] == "pending"
    await kg.resolve_review(db, name, review["uid"], approve=False, note="entity gone")


@pytest.mark.db
async def test_a_review_decided_meanwhile_is_not_applied_twice(domain, monkeypatch):
    db, name = domain
    await kg.ingest(db, name, TEXT, source="t")
    review = (await kgdb.list_reviews(db, name))[0]

    async def lost(*a, **k):
        return False
    monkeypatch.setattr(kgdb, "close_review", lost)
    with pytest.raises(ValueError, match="already decided"):
        await kg.resolve_review(db, name, review["uid"], approve=True)
    rows = await db.cypher(name, "MATCH ()-[r:related_to]->() RETURN r.invalid_at AS gone")
    assert rows and all(r["gone"] for r in rows)                                     # our copy doesn't count


@pytest.mark.db
async def test_a_fact_whose_entity_vanished_while_writing_is_rejected(domain, monkeypatch):
    db, name = domain

    async def gone(*a, **k):
        return None
    monkeypatch.setattr(kgdb, "create_fact", gone)
    r = await kg.ingest(db, name, TEXT, source="t")
    assert r["facts_created"] == 0 and not r["failed"]
    assert [f["reason"] for f in r["facts"] if f["status"] == "rejected"] == ["endpoint vanished"]


# ── Supersession ───────────────────────────────────────────────────────────

@pytest.mark.db
async def test_a_state_is_compared_only_with_the_states_near_it_in_meaning(domain):
    db, name = domain
    for uid, n in (("e_a", "Alpha"), ("e_b", "Beta")):
        await kgdb.create_entity(db, name, {"uid": uid, "name": n, "type": "entity", "keys": [n.lower()]}, None)
    sold = await kgdb.create_fact(db, name, "e_a", "has_state", "e_a", "Alpha is sold out.", [1.0, 0.0, 0.0], "ep", 0.9)
    await kgdb.create_fact(db, name, "e_a", "has_state", "e_a", "Alpha opened in 1902.", [0.0, 1.0, 0.0], "ep", 0.9)
    enables = await kgdb.create_fact(db, name, "e_a", "enables", "e_b", "Alpha enables Beta.", None, "ep", 0.9)
    near = await kgdb.fact_candidates(db, name, "e_a", "has_state", "e_a", [0.9, 0.2, 0.0])
    assert [c["uid"] for c in near] == [sold]
    assert await kgdb.fact_candidates(db, name, "e_a", "has_state", "e_a") == []
    assert [c["uid"] for c in await kgdb.fact_candidates(db, name, "e_a", "enables", "e_b")] == [enables]


@pytest.mark.db
async def test_a_supersession_the_linker_makes_no_more_is_undone_once(domain, monkeypatch):
    db, name = domain
    await kgdb.create_entity(db, name, {"uid": "e_a", "name": "Alpha", "type": "entity", "keys": ["alpha"]}, None)
    old = [await kgdb.create_fact(db, name, "e_a", "has_state", "e_a", t, None, "ep", 0.9)
           for t in ("Alpha is sold out.", "Alpha opened in 1902.")]
    new = await kgdb.create_fact(db, name, "e_a", "has_state", "e_a", "Alpha has stock again.", None, "ep", 0.9)
    for f in old:
        await kgdb.invalidate_fact(db, name, "has_state", f, f"superseded by {new}: Alpha has stock again.")
    asked = []

    async def link_or_none(items):
        asked.append(items)
        return None if len(asked) == 1 else [Link(index=0, supersedes=[old[0]])]   # the model fails the first time
    monkeypatch.setattr(kg, "link_or_none", link_or_none)
    r = await kg.recheck_superseded(db, name)
    assert (r["restored"], r["model_failed"]) == (0, 2)
    r = await kg.recheck_superseded(db, name)
    assert sorted(c["fact"] for c in asked[1][0]["candidates"]) == ["Alpha is sold out.", "Alpha opened in 1902."]
    assert (r["restored"], r["kept"], r["facts"][0]["fact"]) == (1, 1, "Alpha opened in 1902.")
    valid = {f["uid"]: f["valid"] for f in await kgdb.get_facts(db, name, old)}
    assert valid == {old[0]: False, old[1]: True}
    assert (await kg.recheck_superseded(db, name))["superseded"] == 0 and len(asked) == 2    # each asked once
