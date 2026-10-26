"""Storage rules the rest of the KG relies on (core/database.py, domains/registry.py, core/ontology.py): a merge keeps
every fact as it was, a review is decided once, history keeps the first supersession, a deleted domain stays deleted,
a domain may be named in any script. db tests use a throwaway database under a unique id (conftest.scratch)."""

import json

import pytest

from conftest import unique
from core import database as kgdb
from core.database import ArcadeDBError
from domains import registry


async def _entity(db, domain, uid, name, vec=(0.1, 0.2, 0.3)):
    await kgdb.create_entity(db, domain, {"uid": uid, "name": name, "type": "entity", "keys": [name.lower()]},
                             list(vec))


async def _edge(db, domain, uid):
    rows = await db.cypher(domain, "MATCH (a:Entity)-[r:Fact]->(b:Entity) WHERE r.uid = $uid "
                                   "RETURN a.uid AS a, b.uid AS b, properties(r) AS p", uid=uid)
    return rows[0] if rows else None


# ── Merge ──────────────────────────────────────────────────────────────────

@pytest.mark.db
async def test_a_merge_keeps_each_fact_whole_and_points_reviews_at_the_survivor(scratch):
    db, d = scratch
    for uid, name in (("e_dup", "TSMC Ltd"), ("e_keep", "TSMC"), ("e_other", "Apple"), ("e_x", "Nvidia")):
        await _entity(db, d, uid, name)
    f1 = await kgdb.create_fact(db, d, "e_dup", "supplies", "e_other", "TSMC Ltd supplies Apple.", [0.9, 0.1, 0.0],
                                "ep1", 0.8)
    f2 = await kgdb.create_fact(db, d, "e_dup", "has_state", "e_dup", "TSMC Ltd is sold out.", [0.0, 0.9, 0.1],
                                "ep1", 0.7)
    await db.cypher(d, "MATCH ()-[r:supplies]->() WHERE r.uid = $u SET r.relinked = true", u=f1)
    before = {f: (await _edge(db, d, f))["p"] for f in (f1, f2)}
    fact_review = await kgdb.add_review(db, d, "fact", "weak", {
        "source_uid": "e_dup", "source_name": "TSMC Ltd", "relation": "supplies", "target_uid": "e_x",
        "target_name": "Nvidia", "fact": "TSMC Ltd supplies Nvidia.", "reliability": 0.4, "episode_uid": "ep1"})
    same = await kgdb.add_review(db, d, "merge", "same?", {"entity_uid": "e_keep", "entity_name": "TSMC",
                                                           "candidate_uid": "e_dup", "candidate_name": "TSMC Ltd"})
    applying = await kgdb.add_review(db, d, "merge", "same?", {"entity_uid": "e_dup", "candidate_uid": "e_keep"})

    await kgdb.merge_entities(db, d, "e_dup", "e_keep")

    moved = await _edge(db, d, f1)
    assert (moved["a"], moved["b"]) == ("e_keep", "e_other")
    assert moved["p"] == before[f1] and moved["p"]["relinked"] is True        # uid, embedding, every property
    state = await _edge(db, d, f2)
    assert (state["a"], state["b"]) == ("e_keep", "e_keep") and state["p"] == before[f2]
    assert [f["uid"] for f in await kgdb.search_facts(db, d, [0.9, 0.1, 0.0], 1)] == [f1]   # still in the index
    assert not await kgdb.get_entities(db, d, ["e_dup"])
    p = (await kgdb.get_review(db, d, fact_review))["payload"]
    assert (p["source_uid"], p["source_name"]) == ("e_keep", "TSMC")
    assert (await kgdb.get_review(db, d, same))["status"] == "approved"     # nothing left to merge
    assert (await kgdb.get_review(db, d, applying))["status"] == "pending"  # its decider closes it


@pytest.mark.db
async def test_a_merge_into_a_vanished_entity_is_refused(scratch):
    db, d = scratch
    await _entity(db, d, "e_a", "Alpha")
    with pytest.raises(ValueError):
        await kgdb.merge_entities(db, d, "e_a", "e_gone")
    assert await kgdb.get_entities(db, d, ["e_a"])


@pytest.mark.db
async def test_a_fact_on_a_missing_entity_is_not_reported_as_created(scratch):
    db, d = scratch
    await _entity(db, d, "e_a", "Alpha")
    assert await kgdb.create_fact(db, d, "e_a", "uses", "e_gone", "Alpha uses it.", None, "ep1", 0.9) is None
    assert await kgdb.create_fact(db, d, "e_a", "has_state", "e_a", "Alpha is up.", None, "ep1", 0.9)


# ── Facts ──────────────────────────────────────────────────────────────────

@pytest.mark.db
async def test_the_first_supersession_stays_in_history(scratch):
    db, d = scratch
    await _entity(db, d, "e_a", "Alpha")
    f = await kgdb.create_fact(db, d, "e_a", "has_state", "e_a", "Alpha is sold out.", None, "ep1", 0.9)
    await kgdb.invalidate_fact(db, d, "has_state", f, "superseded by f_1")
    await kgdb.invalidate_fact(db, d, "has_state", f, "superseded by f_2")
    assert (await kgdb.get_facts(db, d, [f]))[0]["invalid_reason"] == "superseded by f_1"


@pytest.mark.db
async def test_superseded_paraphrases_do_not_crowd_out_a_valid_fact(scratch):
    db, d = scratch
    await _entity(db, d, "e_a", "Alpha")
    old = [await kgdb.create_fact(db, d, "e_a", "has_state", "e_a", f"Alpha is sold out ({i}).", v, "ep1", 0.9)
           for i, v in enumerate(([1.0, 0.0, 0.0], [0.99, 0.01, 0.0]))]
    for f in old:
        await kgdb.invalidate_fact(db, d, "has_state", f, "superseded")
    valid = await kgdb.create_fact(db, d, "e_a", "has_state", "e_a", "Alpha has stock again.", [0.8, 0.3, 0.0],
                                   "ep2", 0.9)
    assert [f["uid"] for f in await kgdb.search_facts(db, d, [1.0, 0.0, 0.0], 1)] == [valid]


# ── Reviews ────────────────────────────────────────────────────────────────

@pytest.mark.db
async def test_a_review_is_decided_once_and_the_queue_pages(scratch):
    db, d = scratch
    uids = [await kgdb.add_review(db, d, "fact", f"item {i}", {"n": i}) for i in range(5)]
    for i, u in enumerate(uids):   # created_at has seconds: order them explicitly
        await db.sql(d, "UPDATE ReviewItem SET created_at = :t WHERE uid = :u", t=f"2026-10-14T00:00:0{i}+00:00", u=u)
    assert await kgdb.close_review(db, d, uids[0], "approved", "first")
    assert not await kgdb.close_review(db, d, uids[0], "rejected", "second")
    assert (await kgdb.get_review(db, d, uids[0]))["status"] == "approved"
    assert await kgdb.count_reviews(db, d, "pending") == 4
    oldest = await kgdb.list_reviews(db, d, "pending", limit=2, offset=1, oldest_first=True)
    assert [r["uid"] for r in oldest] == uids[2:4]
    assert [r["uid"] for r in await kgdb.list_reviews(db, d, "pending", limit=1)] == [uids[4]]


@pytest.mark.db
async def test_nested_review_values_are_referenced(scratch):
    db, d = scratch
    await kgdb.add_review(db, d, "link", "?", {"fact_uid": "f_new", "supersedes": ["f_old"],
                                               "existing": [{"uid": "f_ex", "relation": "uses"}]})
    keep = await kgdb.referenced(db, d)
    assert {"f_new", "f_old", "f_ex"} <= keep


# ── Episodes ───────────────────────────────────────────────────────────────

@pytest.mark.db
async def test_an_episode_carries_its_ingest_status(scratch):
    db, d = scratch
    await kgdb.create_episode(db, d, {"uid": "ep_1", "source": "s", "content": "x", "content_hash": "h1"})
    assert (await kgdb.find_episode(db, d, "h1"))["status"] == "pending"
    await kgdb.set_episode_status(db, d, "ep_1", "failed", "the model timed out")
    ep = await kgdb.find_episode(db, d, "h1")
    assert ep["uid"] == "ep_1" and ep["status"] == "failed" and ep["created_at"] is None   # no created_at given
    assert [e["status"] for e in await kgdb.list_episodes(db, d)] == ["failed"]
    await db.cypher(d, "CREATE (e:Episode) SET e += $p", p={"uid": "ep_old", "content_hash": "h0"})   # before statuses
    assert (await kgdb.find_episode(db, d, "h0"))["status"] is None


# ── Domains ────────────────────────────────────────────────────────────────

@pytest.mark.db
async def test_a_deleted_domain_is_not_brought_back_by_a_late_write(db):
    domain = unique("gone")
    await kgdb.ensure_domain_db(db, domain)
    await kgdb.drop_domain_db(db, domain)
    with pytest.raises(ArcadeDBError):
        await kgdb.insert_doc(db, domain, "Setting", {"key": "x"})
    with pytest.raises(ArcadeDBError):
        await kgdb.save_report(db, domain, {})
    assert not await db.exists(domain)


def test_every_schema_type_is_reserved_for_relations():
    for name in ("thread", "message", "setting", "chatturn", "entity", "reviewitem"):
        assert kgdb.edge_type(name) == f"rel_{name}"
    assert kgdb.edge_type("supplies") == "supplies"


def test_a_domain_may_be_named_in_any_script(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    assert registry.domain_id("반도체") == "domain" and registry.domain_id("2026 plan") == "domain"
    assert registry.domain_id("Quant Trading") == "quant_trading"
    first = registry.create_domain("반도체", "chips")
    second = registry.create_domain("배터리", "batteries")
    assert first == "domain" and second.startswith("domain_") and second != first
    assert registry.load_domain(second)["config"]["title"] == "배터리"
    # Titles are compared, not slugs: "AI 반도체" and "AI 로봇" are both "ai"
    ai = registry.create_domain("AI 반도체", "chips", mine={first, second})
    assert registry.create_domain("AI 로봇", "robots", mine={first, second, ai}) != ai
    with pytest.raises(ValueError):
        registry.create_domain("  ai   반도체 ", "again", mine={ai})
    assert registry.create_domain("AI 반도체", "someone else's has it", mine=set()) != ai   # not mine: allowed


def test_an_ontology_is_swapped_in_whole(tmp_path, monkeypatch):
    from core.ontology import Ontology, save_ontology

    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    d = registry.create_domain("Onto test", "x")
    save_ontology(d, Ontology(entity_types=["company"], relation_types=["supplies"]))
    assert json.loads(registry.ontology_path(d).read_text())["entity_types"] == ["company"]
    assert [p.name for p in (tmp_path / d).iterdir() if p.name.endswith(".tmp")] == []


async def test_a_new_domain_never_takes_over_or_drops_a_database_without_a_folder(tmp_path, monkeypatch):
    """A database under the id with no folder may be a real graph whose folder is elsewhere (SOVEREIGN_DOMAINS_DIR
    changed): the new domain takes another id, and nothing is dropped."""
    from types import SimpleNamespace

    from api import domains as domains_api
    from core import accounts, auth

    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    calls = []

    class FakeDB:
        async def exists(self, d):
            return d == "orphan_graph"
    for name in ("drop_domain_db", "ensure_domain_db"):
        async def record(db, d, _n=name, **kw):
            calls.append((_n, d))
        monkeypatch.setattr(domains_api, name, record)

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(accounts, "forget_domain", nothing)
    monkeypatch.setattr(accounts, "add_member", nothing)
    monkeypatch.setattr(domains_api, "bootstrap_domain_ontology", nothing)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=FakeDB())))
    reset = auth.current.set(auth.Principal("user", {"uid": "user_a"}, {}))
    try:
        out = await domains_api.api_create_domain(
            domains_api.CreateDomainRequest(name="Orphan Graph", description="x"), domains_api.BackgroundTasks(), req)
    finally:
        auth.current.reset(reset)
    assert out["domain"].startswith("orphan_graph_")
    assert ("drop_domain_db", "orphan_graph") not in calls and ("ensure_domain_db", out["domain"]) in calls
    assert not (tmp_path / "orphan_graph").exists() and (tmp_path / out["domain"]).exists()
