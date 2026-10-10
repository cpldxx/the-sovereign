"""The graph holds facts: an entity is stored only when a fact about it survives, and entities left without any
fact are pruned — except those a pending review or a playbook still refers to. Stand-in agents, real database."""

import pytest

from agents.extractor import ExtractedEntity, ExtractedFact, Extraction
from agents.validator import FactVerdict
from core import database as kgdb
from core import digest, kg
from domains import registry


@pytest.fixture
async def domain(db, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    name = registry.create_domain("Gate test", "A throwaway domain for the ingest gate")
    await kgdb.ensure_domain_db(db, name)

    async def extract(text, *a, **k):
        return Extraction(
            entities=[ExtractedEntity(name="Acme Labs", type="entity", description="a lab"),
                      ExtractedEntity(name="Widget Engine", type="tool", description="an engine"),
                      ExtractedEntity(name="Site Menu", type="concept", description="navigation text"),
                      ExtractedEntity(name="Weak Gizmo", type="tool", description="a gizmo")],
            facts=[ExtractedFact(source="Acme Labs", relation="enables", target="Widget Engine",
                                 fact="Acme Labs enables the Widget Engine."),
                   ExtractedFact(source="Weak Gizmo", relation="related_to", target="Acme Labs",
                                 fact="Weak Gizmo is related to Acme Labs.")])

    async def validate(text, source, facts):
        return [FactVerdict(index=i, supported=True, reliability=0.9 if "enables" in f else 0.4)
                for i, f in enumerate(facts)]

    async def embed(texts):
        return [None] * len(texts)

    async def resolve(items):
        return []

    async def link(items):
        return []
    for fn in (extract, validate, embed, resolve, link):
        monkeypatch.setattr(kg, fn.__name__, fn)
    yield db, name
    await kgdb.drop_domain_db(db, name)


async def _names(db, domain):
    return {r["name"] for r in await db.cypher(domain, "MATCH (e:Entity) RETURN e.name AS name")}


@pytest.mark.db
async def test_a_name_without_a_fact_is_not_stored(domain):
    db, name = domain
    text = "Acme Labs enables the Widget Engine. Site Menu. Weak Gizmo is related to Acme Labs. " * 3
    report = await kg.ingest(db, name, text, source="test")
    assert await _names(db, name) == {"Acme Labs", "Widget Engine", "Weak Gizmo"}   # Weak Gizmo: its fact waits in review
    dropped = [e for e in report["entities"] if e["status"] == "dropped"]
    assert [(e["name"], e["reason"]) for e in dropped] == [("Site Menu", "no fact about it in the source")]
    assert report["facts_created"] == 1 and report["review_items"] == 1


@pytest.mark.db
async def test_entities_left_without_facts_are_pruned_unless_still_referred_to(domain):
    db, name = domain
    text = "Acme Labs enables the Widget Engine. Weak Gizmo is related to Acme Labs. " * 3
    await kg.ingest(db, name, text, source="test")
    # Weak Gizmo has no fact (its fact waits in review): the review protects it
    assert [e["name"] for e in await digest.prune_entities(db, name, dry_run=True)] == []
    review = (await kgdb.list_reviews(db, name))[0]
    await kg.resolve_review(db, name, review["uid"], approve=False, note="test")
    doomed = await digest.prune_entities(db, name, dry_run=True)
    assert [e["name"] for e in doomed] == ["Weak Gizmo"] and doomed[0]["episodes"]
    assert "Weak Gizmo" in await _names(db, name)                                  # a dry run deletes nothing
    await digest.prune_entities(db, name)
    assert await _names(db, name) == {"Acme Labs", "Widget Engine"}


@pytest.mark.db
async def test_a_relation_filed_as_a_state_is_linked(domain, monkeypatch):
    """At ingest: a has_state fact naming another entity becomes the relation it states, checked as that relation."""
    db, name = domain
    from agents.extractor import Relation

    async def extract(text, *a, **k):
        return Extraction(
            entities=[ExtractedEntity(name="Acme Labs", type="entity"), ExtractedEntity(name="Widget Engine", type="tool")],
            facts=[ExtractedFact(source="Widget Engine", relation="has_state", target="Widget Engine",
                                 fact="Acme Labs is testing its Widget Engine for launch.")])
    checked = []

    async def validate(text, source, facts):
        checked.extend(facts)
        return [FactVerdict(index=i, supported=True, reliability=0.9) for i, _ in enumerate(facts)]

    async def relate(items, relations):
        assert items[0]["entities"] == ["Widget Engine", "Acme Labs"]
        return [Relation(index=0, source="Acme Labs", relation="enables", target="Widget Engine")]
    monkeypatch.setattr(kg, "extract", extract)
    monkeypatch.setattr(kg, "validate", validate)
    monkeypatch.setattr(kg, "relate", relate)
    report = await kg.ingest(db, name, "Acme Labs is testing its Widget Engine for launch. " * 3, source="test")
    assert checked == ["Acme Labs enables Widget Engine."]
    rows = await db.cypher(name, "MATCH (a:Entity)-[r:Fact]->(b:Entity) RETURN a.name AS a, type(r) AS rel, b.name AS b, r.fact AS fact")
    assert [(r["a"], r["rel"], r["b"]) for r in rows] == [("Acme Labs", "enables", "Widget Engine")]
    assert rows[0]["fact"] == "Acme Labs is testing its Widget Engine for launch." and report["facts_created"] == 1


@pytest.mark.db
async def test_stored_states_are_relinked_once(domain, monkeypatch):
    """Backfill: the relation a stored state states is added (the state stays), and the state is read only once."""
    db, name = domain
    from agents.extractor import Relation

    async def extract(text, *a, **k):
        return Extraction(
            entities=[ExtractedEntity(name="Acme Labs", type="entity"), ExtractedEntity(name="Widget Engine", type="tool"),
                      ExtractedEntity(name="Other Thing", type="tool")],
            facts=[ExtractedFact(source="Widget Engine", relation="has_state", target="Widget Engine",
                                 fact="Widget Engine shipped 5 units."),
                   ExtractedFact(source="Acme Labs", relation="related_to", target="Other Thing",
                                 fact="Acme Labs is related to Other Thing.")])

    async def no_relations(items, relations):
        return []

    async def validate(text, source, facts):
        return [FactVerdict(index=i, supported=True, reliability=0.9) for i, _ in enumerate(facts)]
    monkeypatch.setattr(kg, "extract", extract)
    monkeypatch.setattr(kg, "validate", validate)
    monkeypatch.setattr(kg, "relate", no_relations)
    await kg.ingest(db, name, "Widget Engine shipped 5 units. Acme Labs is related to Other Thing. " * 3, source="t")
    # a state stored before this fix, naming another entity
    await db.cypher(name, "MATCH (w:Entity {name: 'Widget Engine'})-[r:has_state]->(w) "
                          "SET r.fact = 'Acme Labs shipped 5 units of its Widget Engine.'")
    calls = []

    async def relate(items, relations):
        calls.append(items)
        return [Relation(index=0, source="Acme Labs", relation="enables", target="Widget Engine")]
    monkeypatch.setattr(kg, "relate", relate)
    preview = await kg.relink_states(db, name, dry_run=True)
    assert preview["added"] == 1 and preview["facts"][0]["relation"] == "enables"
    done = await kg.relink_states(db, name)
    assert done["added"] == 1
    rels = {r["rel"] for r in await db.cypher(name, "MATCH (:Entity)-[r:Fact]->(:Entity) RETURN type(r) AS rel")}
    assert {"has_state", "enables", "related_to"} <= rels
    again = await kg.relink_states(db, name)
    assert again["statements"] == 0 and len(calls) == 2              # dry run + real run; the third reads nothing
