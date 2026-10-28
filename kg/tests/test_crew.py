"""The Head's team: tasks out on the queue, reports back on threads, the Head woken within its guard.

Runs on a throwaway domain in a temporary domains directory (the real domains are never touched); agents are
stand-ins, so no model is needed — the real roster is checked for shape only."""

import asyncio
import secrets

import pytest
from pydantic import BaseModel, Field

from core import crew
from core import database as kgdb
from domains import registry


class Echo(BaseModel):
    text: str = Field(min_length=2, description="What to echo")


async def _echo(db, domain, x: Echo) -> crew.Outcome:
    return crew.Outcome(f"echo: {x.text}", {"n": len(x.text)})


async def _slow(db, domain, x: Echo) -> crew.Outcome:
    await asyncio.sleep(0.5)
    return crew.Outcome(f"slow: {x.text}")


async def _boom(db, domain, x: Echo) -> crew.Outcome:
    raise RuntimeError("the source is down")


async def _desk(db, domain) -> str:
    return "I know that the answer is 42."


@pytest.fixture
async def team(db, tmp_path, monkeypatch):
    """A temporary domain with a stand-in team; the Head's wake is captured instead of calling Hermes."""
    monkeypatch.setattr(registry, "DOMAINS_DIR", tmp_path)
    # unique per run: two runs at once (or a run beside a leftover database) must not share one
    domain = registry.create_domain(f"Crew test {secrets.token_hex(4)}", "A throwaway domain for the crew tests")
    await kgdb.ensure_domain_db(db, domain)
    monkeypatch.setattr(crew, "ROSTER", {"echo": crew.Member("echo", "Echo", "repeats things", _desk, {
        "say": crew.Task("say", "Echo a text", Echo, _echo),
        "slow": crew.Task("slow", "Echo slowly", Echo, _slow, background=True),
        "fail": crew.Task("fail", "Always fails", Echo, _boom)})})

    async def answer(db_, domain_, member, msg):
        return crew.Outcome("42, from my desk")
    monkeypatch.setattr(crew, "_answer", answer)
    woken: list[str] = []

    async def head(domain_, message):
        woken.append(message)
        return "Noted; nothing to store."
    monkeypatch.setattr(crew, "_ask_head", head)
    monkeypatch.setattr(crew, "HEAD_WAKE", True)
    crew._wakes.clear()
    loop = asyncio.create_task(crew.work_loop(db, idle=0.2))
    yield db, domain, woken
    loop.cancel()
    for t in list(crew._background):
        t.cancel()
    await kgdb.drop_domain_db(db, domain)


async def _until(predicate, seconds: float = 10):
    for _ in range(int(seconds / 0.1)):
        if await predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def test_the_real_roster_is_the_researcher_and_the_learning_pipeline():
    """Two teammates; every agent module is a station of the learning pipeline, every task runs at one, and every
    task's inputs are a model."""
    import pkgutil
    import agents
    modules = {m.name for m in pkgutil.iter_modules(agents.__path__)} - {"__init__"}
    assert set(crew.ROSTER) == {"research", "learning"}
    assert modules == set(crew.STATIONS), modules ^ set(crew.STATIONS)
    for member in crew.ROSTER.values():
        for t in member.tasks.values():
            assert t.station in (*crew.STATIONS, "research"), t.name
    for card in crew.roster():
        assert card["role"] and card["title"]
        for t in card["tasks"]:
            assert isinstance(t["inputs"], dict)


@pytest.mark.db
async def test_a_task_is_checked_before_it_is_queued(team):
    db, domain, _ = team
    with pytest.raises(crew.CrewError, match="Send inputs like"):
        await crew.assign(db, domain, "echo", "say", {"text": ""})
    with pytest.raises(crew.CrewError, match="Its tasks: say, slow, fail"):
        await crew.assign(db, domain, "echo", "dance", {})
    with pytest.raises(crew.CrewError, match="The team: echo"):
        await crew.assign(db, domain, "nobody", "say", {"text": "hi"})


@pytest.mark.db
async def test_a_finished_task_is_reported_and_wakes_the_head(team):
    db, domain, woken = team
    out = await crew.assign(db, domain, "echo", "say", {"text": "hello"})

    async def noted():
        t = await crew.thread(db, domain, out["thread"])
        return t and any(m["kind"] == "note" for m in t["messages"])
    assert await _until(noted), await crew.thread(db, domain, out["thread"])
    t = await crew.thread(db, domain, out["thread"])
    kinds = [(m["sender"], m["recipient"], m["kind"], m["status"]) for m in t["messages"]]
    assert kinds == [("head", "echo", "task", "done"), ("echo", "head", "report", "read"),
                     ("head", "echo", "note", "read")], kinds
    assert t["status"] == "done" and t["wakes"] == 1
    assert "echo: hello" in woken[0] and out["thread"] in woken[0]
    assert await crew.unread(db, domain) == 0


@pytest.mark.db
async def test_a_failure_is_reported_too(team):
    db, domain, woken = team
    out = await crew.assign(db, domain, "echo", "fail", {"text": "x1"})

    async def failed():
        t = await crew.thread(db, domain, out["thread"])
        return t["status"] == "failed" and len(woken) == 1
    assert await _until(failed)
    report = (await crew.thread(db, domain, out["thread"]))["messages"][1]
    assert "the source is down" in report["text"] and report["data"]["ok"] is False


@pytest.mark.db
async def test_a_question_is_answered_while_the_head_waits(team):
    db, domain, woken = team
    out = await crew.ask(db, domain, "echo", "What is the answer?", wait=10)
    assert out["answer"] == "42, from my desk"
    assert not woken and await crew.unread(db, domain) == 0   # delivered inline, not to the inbox


@pytest.mark.db
async def test_background_work_does_not_hold_the_queue(team):
    db, domain, _ = team
    slow = await crew.assign(db, domain, "echo", "slow", {"text": "takes a while"})
    quick = await crew.ask(db, domain, "echo", "Still there?", wait=5)
    assert quick["answer"]                                     # answered while the slow task still runs

    async def reported():
        t = await crew.thread(db, domain, slow["thread"])
        return any(m["kind"] == "report" for m in t["messages"])
    assert await _until(reported)


@pytest.mark.db
async def test_the_wake_guard_leaves_reports_in_the_inbox(team, monkeypatch):
    db, domain, woken = team
    monkeypatch.setattr(crew, "MAX_WAKES", 1)
    first = await crew.assign(db, domain, "echo", "say", {"text": "one"})
    assert await _until(lambda: _done(db, domain, first["thread"], notes=1))
    await crew.assign(db, domain, "echo", "say", {"text": "two"}, thread=first["thread"])

    async def waiting():
        return await crew.unread(db, domain) == 1
    assert await _until(waiting)
    assert len(woken) == 1                                     # the second report didn't wake the Head
    left = await crew.inbox(db, domain)
    assert [m["text"] for m in left] == ["echo: two"] and await crew.unread(db, domain) == 0


async def _done(db, domain, thread, notes):
    t = await crew.thread(db, domain, thread)
    return sum(m["kind"] == "note" for m in t["messages"]) == notes


@pytest.mark.db
async def test_work_interrupted_by_a_restart_is_reported(team):
    db, domain, woken = team
    uid = await crew._open(db, domain, "echo", "half done", "head", None)
    msg = await crew._post(db, domain, uid, "head", "echo", "task", "say: x", {"inputs": {"text": "xx"}},
                           task="say", status=crew.WORKING)
    await crew._recover(db)
    t = await crew.thread(db, domain, uid)
    assert t["messages"][0]["status"] == "failed"
    assert "Interrupted by a restart" in t["messages"][1]["text"]
    assert msg["uid"] == t["messages"][0]["uid"]


@pytest.mark.db
async def test_notices_wait_for_the_head_without_waking_it(team):
    db, domain, woken = team
    await crew.notify(db, domain, "echo", "The morning report is ready.")
    await asyncio.sleep(0.5)
    assert not woken and await crew.unread(db, domain) == 1
    team_state = await crew.team(db, domain)
    assert team_state[0]["state"] == "idle"


@pytest.mark.parametrize("written,name", [("check", "check"), ("check(claims=[{text: 'a'}])", "check"),
                                          ("validator.check", "check"), (" find_sources ", "find_sources"),
                                          ("", "")])
def test_a_task_written_as_a_call_is_read_by_its_name(written, name):
    assert crew._task_name(written) == name


def test_loose_inputs_become_the_tasks_fields_and_are_still_checked():
    """What a local model sends — a JSON string, {"text": …} items, one text for a list — is reshaped where the
    meaning is unambiguous; the task's model still decides."""
    shaped = crew._shape(crew.Check, {"claims": [{"text": "A", "uid": "c1"}, {"claim": "B"}], "source_text": {"text": "xx"}})
    assert shaped == {"claims": ["A", "B"], "source_text": "xx"}
    assert crew._shape(crew.Check, '{"claims": "one", "source_text": "t"}') == {"claims": ["one"], "source_text": "t"}
    assert crew._shape(crew.Investigate, "What is HBM4 pricing?") == {"question": "What is HBM4 pricing?"}
    assert crew._shape(crew.Check, "not json, two required fields") == {}
    with pytest.raises(Exception):
        crew.Check.model_validate(crew._shape(crew.Check, {"claims": [{"a": 1, "b": 2}], "source_text": "x" * 30}))


def test_an_agent_is_found_by_name_or_title_and_a_stations_old_name_reaches_learning():
    assert crew._member_of("The Researcher").name == "research"
    assert crew._member_of("learning pipeline").name == "learning"
    assert crew._member_of("The Validator").name == "learning"
    assert crew._member_of("strategist agent").name == "learning"
    assert crew._teammate("reporter") == "learning" and crew._teammate("research") == "research"
    with pytest.raises(crew.CrewError):
        crew._member_of("intern")


def test_the_roster_shows_inputs_as_an_example():
    card = next(t for t in crew.ROSTER["learning"].card()["tasks"] if t["name"] == "check")
    assert card["example"] == {"claims": ["…"], "source_text": "…"} and card["optional"] == ["source"]


@pytest.mark.db
async def test_the_head_works_the_review_queue_until_it_is_empty(team, monkeypatch):
    db, domain, _ = team
    for i in range(5):
        await kgdb.add_review(db, domain, "fact", f"weak fact {i}", {"fact": f"fact {i}"})

    async def head(domain_, message, max_iterations=12):
        assert "review queue" in message
        pending = await kgdb.list_reviews(db, domain_)
        for r in pending[:2]:                                    # decides two per turn
            await kgdb.close_review(db, domain_, r["uid"], "rejected", "test")
        return "Rejected 2."
    monkeypatch.setattr(crew, "_ask_head", head)
    out = await crew.review_sweep(db, domain, minutes=5)
    assert out == {"pending": 0, "decided": 5, "rounds": 3, "thread": out["thread"]}
    notes = (await crew.thread(db, domain, out["thread"]))["messages"]
    assert [m["data"]["left"] for m in notes] == [3, 1, 0]


@pytest.mark.db
async def test_a_review_round_that_decides_nothing_ends_the_sweep(team, monkeypatch):
    db, domain, _ = team
    await kgdb.add_review(db, domain, "merge", "same thing?", {"entity_uid": "e1"})
    calls = []

    async def head(domain_, message, max_iterations=12):
        calls.append(1)
        return "I could not decide."
    monkeypatch.setattr(crew, "_ask_head", head)
    out = await crew.review_sweep(db, domain, minutes=5)
    assert len(calls) == 1 and out["decided"] == 0 and out["pending"] == 1


def test_the_activity_feed_shows_who_works_and_what_moves():
    from core import activity
    d = "activity_test"
    start = activity.snapshot(d)["seq"]
    with activity.working(d, "extractor", "page one"), activity.working(d, "extractor", "page two"):
        activity.event(d, "head", "research", "task", "investigate: HBM supply")
        snap = activity.snapshot(d, start)
        assert snap["working"] == [{"agent": "extractor", "since": snap["working"][0]["since"],
                                    "doing": ["page one", "page two"]}]
        assert [(e["source"], e["target"], e["kind"]) for e in snap["events"]] == [("head", "research", "task")]
    after = activity.snapshot(d, snap["seq"])
    assert after["working"] == [] and after["events"] == []


async def _quiet_desk(db, domain) -> str:
    return ""


@pytest.mark.db
async def test_one_review_sweep_per_domain_at_a_time(team, monkeypatch):
    db, domain, _ = team
    await kgdb.add_review(db, domain, "fact", "weak fact", {"fact": "f"})
    crew._reviewing.add(domain)
    try:
        out = await crew.review_sweep(db, domain, minutes=1)
    finally:
        crew._reviewing.discard(domain)
    assert out["busy"] and out["pending"] == 1 and out["decided"] == 0


async def _async(value):
    return value


@pytest.mark.db
async def test_a_conversation_is_kept_per_person_and_survives_a_reload(team):
    from core import chat
    db, domain, _ = team
    await chat.add(db, domain, "u1", "user", "What changed?")
    pending = await chat.add(db, domain, "u1", "assistant", "", status="pending")
    await chat.add(db, domain, "u2", "user", "someone else's question")
    turns = await chat.turns(db, domain, "u1")
    assert [(t["role"], t["status"]) for t in turns] == [("user", "done"), ("assistant", "pending")]
    # the answer lands later (Hermes), whoever reloaded meanwhile; only the person's own turn can be changed
    assert not await chat.update(db, domain, "u2", pending["uid"], {"content": "hijack"})
    assert await chat.update(db, domain, "u1", pending["uid"], {"content": "HBM is tight.", "status": "done",
                                                               "steps": [{"id": "s", "said": "Let me check."}]})
    last = (await chat.turns(db, domain, "u1"))[-1]
    assert last["content"] == "HBM is tight." and last["status"] == "done" and last["steps"][0]["said"]
    # a browser's old local chat moves in only once, into an empty conversation
    assert await chat.import_turns(db, domain, "u3", [{"role": "user", "content": "old"}]) == 1
    assert await chat.import_turns(db, domain, "u3", [{"role": "user", "content": "again"}]) == 0
    await chat.clear(db, domain, "u1")
    assert await chat.turns(db, domain, "u1") == [] and len(await chat.turns(db, domain, "u2")) == 1


def test_anyone_in_a_domain_may_keep_a_conversation_with_its_head():
    from core import auth
    assert auth.needed("POST", "/domains/d/chat") == ("d", "viewer")
    assert auth.needed("PATCH", "/domains/d/chat/chat_1") == ("d", "viewer")
    assert auth.needed("DELETE", "/domains/d/chat") == ("d", "viewer")
    assert auth.needed("POST", "/domains/d/ingest") == ("d", "editor")


@pytest.mark.db
async def test_forgetting_retires_unconfirmed_weak_facts_and_what_only_they_held(team):
    from core import learning
    db, domain, _ = team
    await db.cypher(domain, "CREATE (:Entity {uid: 'e_a', name: 'A'}), (:Entity {uid: 'e_b', name: 'B'}), "
                            "(:Entity {uid: 'e_c', name: 'C'})")
    from core.database import edge_type
    old, rel = "2026-01-01T00:00:00+00:00", edge_type("supplies")
    await db.sql(domain, f"CREATE EDGE TYPE `{rel}` IF NOT EXISTS EXTENDS Fact")
    for uid, a, b, ev, dis, fact in [("f_weak", "e_a", "e_b", 1, 0.7, "A may supply B (one blog)"),
                                     ("f_strong", "e_a", "e_c", 3, 0.1, "A supplies C"),
                                     ("f_fresh", "e_c", "e_b", 1, 0.7, "C may supply B")]:
        created = old if uid != "f_fresh" else "2099-01-01T00:00:00+00:00"
        await db.cypher(domain, f"MATCH (a:Entity {{uid: $a}}), (b:Entity {{uid: $b}}) CREATE (a)-[:`{rel}` "
                                "{uid: $uid, fact: $fact, evidence: $ev, disbelief: $dis, created_at: datetime($c)}]->(b)",
                        a=a, b=b, uid=uid, fact=fact, ev=ev, dis=dis, c=created)
    dry = await learning.forget(db, domain, 30, dry_run=True)
    assert dry["facts"] == 1 and dry["entities"] == 0          # B still has the fresh fact, A the strong one
    out = await learning.forget(db, domain, 30)
    assert out == dry
    rows = await db.cypher(domain, "MATCH ()-[r:Fact]->() RETURN r.uid AS uid, r.invalid_reason AS why")
    assert {r["uid"]: (r["why"] or "") for r in rows}["f_weak"].startswith("forgotten")
    assert learning.FORGOTTEN not in ({r["uid"]: (r["why"] or "") for r in rows}["f_strong"])


# ── Regressions (2026-10-14 review) ────────────────────────────────────────

@pytest.mark.db
async def test_a_report_that_lost_the_race_for_the_wake_is_woken_after(team):
    """An agent's report on a thread the Head didn't open, refused while the Head was busy, gets its turn once the
    Head is free (only Head-opened threads counted before)."""
    db, domain, woken = team
    crew._waking.add(domain)                                    # the Head is busy with another domain's turn
    try:
        await crew.report(db, domain, "echo", "news — X", "X happened; decide what follows")
        await asyncio.sleep(0.3)
    finally:
        crew._waking.discard(domain)
    assert not woken and await crew._waiting_report(db, domain)
    await crew.assign(db, domain, "echo", "say", {"text": "hello"})
    assert await _until(lambda: _async(len(woken) == 2))
    assert any("X happened" in w for w in woken) and await crew.unread(db, domain) == 0


@pytest.mark.db
async def test_a_failed_wake_spends_nothing_and_leaves_the_report_unread(team, monkeypatch):
    db, domain, woken = team

    async def broken(domain_, message):
        raise ValueError("Expecting value: line 1 column 1")      # Hermes answered something that isn't JSON
    monkeypatch.setattr(crew, "_ask_head", broken)
    await crew.report(db, domain, "echo", "fired", "something to decide")
    assert await _until(lambda: _async(not crew._waking))
    await asyncio.sleep(0.3)
    thread = await crew._waiting_report(db, domain)
    assert thread and (await kgdb.get_doc(db, domain, "Thread", thread))["wakes"] == 0
    assert await crew.unread(db, domain) == 1 and not crew._wakes[domain]


@pytest.mark.db
async def test_a_message_claimed_elsewhere_is_not_run_twice(team):
    db, domain, woken = team
    uid = await crew._open(db, domain, "echo", "claimed", "head", None)
    msg = await crew._post(db, domain, uid, "head", "echo", "task", "say: x", {"inputs": {"text": "xx"}},
                           task="say", status=crew.WORKING)            # another KG process took it
    await crew._work(db, domain, {**msg, "status": crew.QUEUED})
    t = await crew.thread(db, domain, uid)
    assert len(t["messages"]) == 1 and t["messages"][0]["status"] == crew.WORKING


@pytest.mark.db
async def test_a_consulted_agent_sees_the_latest_messages_of_its_thread(team):
    db, domain, _ = team
    uid = await crew._open(db, domain, "echo", "long", "head", None)
    for i in range(12):
        await crew._post(db, domain, uid, "head", "user", "note", f"line {i}", status=crew.READ)
        await asyncio.sleep(1.01) if i in (3, 10) else None        # created_at has seconds: keep the order clear
    history = await crew._history(db, domain, {"thread": uid, "uid": ""})
    assert "line 11" in history and "line 0" not in history and history.index("line 10") < history.index("line 11")


async def test_the_activity_feed_does_not_repeat_the_task_name(monkeypatch):
    seen = []
    monkeypatch.setattr(crew.activity, "event", lambda d, s, r, k, text="": seen.append(text))

    async def insert(db, domain, type_, doc, json_fields=()):
        return {**doc, "uid": "mess_0123456789ab", "created_at": "now"}

    async def update(*a, **kw):
        return True
    monkeypatch.setattr(kgdb, "insert_doc", insert)
    monkeypatch.setattr(kgdb, "update_doc", update)
    await crew._post(None, "d", "t", "head", "nobody", "task", "check: two claims", task="check", status="read")
    await crew._post(None, "d", "t", "head", "nobody", "task", "two claims", task="check", status="read")
    assert seen == ["check: two claims", "check: two claims"]


def test_a_review_queue_the_head_cannot_decide_waits_until_it_changes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(crew.time, "monotonic", lambda: clock[0])
    crew._fruitless.pop("rv", None)
    mark = (3, "a", "b")
    assert not crew._skip_review("rv", mark)
    crew._after_review("rv", {"decided": 0, "rounds": 1}, mark, every=30)
    assert crew._skip_review("rv", mark)                        # same queue: wait
    clock[0] += 31 * 60
    assert not crew._skip_review("rv", (4, "a", "c"))          # it changed: sweep again
    crew._after_review("rv", {"decided": 0, "rounds": 1}, (4, "a", "c"), every=30)
    clock[0] += 31 * 60
    assert crew._skip_review("rv", (5, "a", "d"))              # second fruitless sweep: 60 min back-off
    clock[0] += 7 * 3600
    assert not crew._skip_review("rv", (4, "a", "c"))          # never more than 6 h
    crew._after_review("rv", {"decided": 2, "rounds": 1}, (2, "a", "c"), every=30)
    assert "rv" not in crew._fruitless
    crew._after_review("rv", {"decided": 0, "unreachable": True}, mark, every=30)
    assert "rv" not in crew._fruitless                          # Hermes down is not the Head failing to decide


async def test_a_background_review_sweep_runs_one_at_a_time(monkeypatch):
    monkeypatch.setattr(crew.registry, "load_domain", lambda d: {})
    ran = asyncio.Event()

    async def sweep(db, domain, minutes):
        ran.set()
        return {"decided": 0}
    monkeypatch.setattr(crew, "_sweep", sweep)
    assert crew.start_review_sweep(None, "rv2", 1) is True
    assert crew.start_review_sweep(None, "rv2", 1) is False    # the first still holds the domain
    await asyncio.wait_for(ran.wait(), 2)
    await asyncio.sleep(0)
    assert "rv2" not in crew._reviewing


@pytest.mark.db
async def test_learnings_conversations_include_those_its_stations_had(scratch):
    db, domain = scratch
    for agent in ("validator", "learning", "research"):
        await kgdb.insert_doc(db, domain, "Thread", {"title": agent, "agent": agent, "opened_by": "head",
                                                      "status": "done", "wakes": 0, "updated_at": kgdb.now()})
    assert sorted(t["agent"] for t in await crew.threads(db, domain, "learning")) == ["learning", "validator"]
    assert [t["agent"] for t in await crew.threads(db, domain, "research")] == ["research"]
    assert len(await crew.threads(db, domain)) == 3
