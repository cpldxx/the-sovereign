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


def test_the_real_roster_is_the_whole_team():
    """Every agent module has a place on the team, and every task's inputs are a model."""
    import pkgutil
    import agents
    modules = {m.name for m in pkgutil.iter_modules(agents.__path__)}
    # the pipeline agents are on it by name; the Scout and the Coder too; research is the DeerFlow service
    assert modules <= set(crew.ROSTER) | {"__init__"}, modules - set(crew.ROSTER)
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


def test_an_agent_is_found_by_name_or_title():
    assert crew._member_of("The Validator").name == "validator"
    assert crew._member_of("scout agent").name == "scout"
    with pytest.raises(crew.CrewError):
        crew._member_of("intern")


def test_the_roster_shows_inputs_as_an_example():
    card = crew.ROSTER["validator"].card()["tasks"][0]
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
        activity.event(d, "head", "scout", "task", "find_sources: stock price")
        snap = activity.snapshot(d, start)
        assert snap["working"] == [{"agent": "extractor", "since": snap["working"][0]["since"],
                                    "doing": ["page one", "page two"]}]
        assert [(e["source"], e["target"], e["kind"]) for e in snap["events"]] == [("head", "scout", "task")]
    after = activity.snapshot(d, snap["seq"])
    assert after["working"] == [] and after["events"] == []


async def _quiet_desk(db, domain) -> str:
    return ""


@pytest.fixture
def operators(monkeypatch):
    """The Watcher and the Operator, beside the stand-in team."""
    monkeypatch.setitem(crew.ROSTER, "watcher", crew.Member("watcher", "Watcher", "watches playbooks", _quiet_desk))
    monkeypatch.setitem(crew.ROSTER, "operator", crew.Member("operator", "Operator", "runs actions", _quiet_desk))


@pytest.mark.db
async def test_a_fired_playbook_goes_to_the_head_and_nothing_acts_on_its_own(team, operators):
    from core import actions, playbooks
    db, domain, woken = team
    pb = {"uid": "pb1", "name": "HBM squeeze", "response": "warn the team"}
    alert = {"name": "alert"}
    out = await playbooks._escalate(db, domain, pb, alert, {"title": "HBM", "message": "prices up"},
                                    "Playbook “HBM squeeze”: prices up 12%", ["f1"], "prices up")
    assert out["status"] == "reported" and out["action"] == "alert"
    assert await _until(lambda: _async(len(woken) == 1))
    assert "playbook fired" in woken[0] and "alert(" in woken[0] and "Decide" in woken[0]
    assert await actions.list_proposals(db, domain) == []        # the Watcher proposed nothing itself


@pytest.mark.db
async def test_the_operator_reports_a_confirmed_action_and_its_result_to_the_head(team, operators):
    db, domain, woken = team
    done = {"uid": "p1", "action": "simulate_mix", "params": {"a": "x", "b": "y"}, "status": "executed",
            "source": "head", "result": {"status_code": 200, "response": "{\"yield\": 0.82}"}}
    await crew.action_outcome(db, domain, done)
    assert await _until(lambda: _async(len(woken) == 1))
    assert "simulate_mix" in woken[0] and "0.82" in woken[0]
    # declined: a notice, no wake; someone else's proposal: nothing
    await crew.action_outcome(db, domain, {**done, "uid": "p2", "status": "rejected", "note": "not now"})
    await crew.action_outcome(db, domain, {**done, "uid": "p3", "source": "user"})
    await asyncio.sleep(0.3)
    assert len(woken) == 1 and await crew.unread(db, domain) == 1


@pytest.mark.db
async def test_an_auto_connector_runs_when_the_head_says_so(db, team, monkeypatch):
    from core import actions
    _, domain, _ = team
    monkeypatch.setattr(actions, "ALLOW_PRIVATE_WEBHOOKS", True)
    await actions.add_webhook(db, domain, "simulate_mix", "Run the mixing simulation", "http://127.0.0.1:9/sim",
                              [{"name": "first", "type": "string"}], auto=True)
    await actions.add_webhook(db, domain, "order_parts", "Order parts", "http://127.0.0.1:9/order",
                              [{"name": "first", "type": "string"}])
    cat = {a["name"]: a for a in await actions.catalog(db, domain)}
    assert cat["simulate_mix"]["confirm"] is False and cat["order_parts"]["confirm"] is True
    ran = await actions.propose(db, domain, "simulate_mix", {"first": "x"}, "test")
    waits = await actions.propose(db, domain, "order_parts", {"first": "x"}, "test")
    assert ran["status"] in ("executed", "failed") and ran["decided_by"] == "auto"   # nothing listens on :9
    assert waits["status"] == "proposed"


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
async def test_propose_action_takes_a_rationale_tucked_into_params(db, team, monkeypatch):
    """The local model's slip (measured 2026-10-13): rationale inside params, or none at all."""
    from api import mcp
    _, domain, _ = team
    monkeypatch.setattr(mcp, "db", db)
    monkeypatch.setattr(mcp, "_require", lambda d: None)
    p = await mcp.propose_action(domain, "alert", {"title": "E2E", "message": "Trainium 3 is AWS-only",
                                                               "rationale": "the graph says so", "evidence": ["f1"]})
    assert p["status"] == "executed" and p["rationale"] == "the graph says so" and p["evidence"] == ["f1"]
    q = await mcp.propose_action(domain, "alert", {"title": "E2E", "message": "no reason given"})
    assert q["status"] == "executed" and q["rationale"] == "no reason given"


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
async def test_an_alert_reads_without_uids_or_weights(db, team, monkeypatch):
    from api import mcp
    _, domain, _ = team
    monkeypatch.setattr(mcp, "db", db)
    monkeypatch.setattr(mcp, "_require", lambda d: None)
    p = await mcp.propose_action(domain, "alert", {"title": "HBM 부족", "message":
                                 "Micron 확인(f_fad13044abd5, weight=1.0), 2028년까지 지속, weight=0.9"}, "why", ["f_fad13044abd5"])
    assert p["params"]["message"] == "Micron 확인, 2028년까지 지속" and p["evidence"] == ["f_fad13044abd5"]


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
    """A Watcher / Operator report on a thread the Head didn't open, refused while the Head was busy, gets its turn
    once the Head is free (only Head-opened threads counted before)."""
    db, domain, woken = team
    crew._waking.add(domain)                                    # the Head is busy with another domain's turn
    try:
        await crew.report(db, domain, "echo", "playbook fired — X", "X happened; it calls for alert(…)")
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


# ── Playbooks: live triggers, the nightly rewrite ──────────────────────────

class Playbooks:
    """Stand-ins for what check_triggers reads and writes."""

    def __init__(self, monkeypatch, reading: dict, kinds: list[str]):
        from core import playbooks, sensors
        self.notes, self.fired = [], []
        pb = {"uid": "play_0123456789ab", "name": "NVDA drop", "action": "alert", "evidence": [], "response": "warn",
              "trigger": {"sensor": "quote", "params": {"symbol": "NVDA"}, "field": "change_percent", "op": "<=",
                          "value": -5, "every_minutes": 60}}

        async def list_playbooks(db, domain, status="active"):
            return [dict(pb)]

        async def read(db, domain, name, params=None, verify_field=None):
            return reading

        async def members(db, domain, name, with_code=True):
            return [{"kind": k} for k in kinds]

        async def update(db, domain, type_, uid, changes, json_fields=(), if_status=None):
            if "trigger_checked_at" in changes:
                self.notes.append(changes["trigger_note"])
            return True

        async def fire(db, domain, pb_, reading_, why):
            self.fired.append(why)
            return {"playbook": pb_["name"]}
        monkeypatch.setattr(playbooks, "list_playbooks", list_playbooks)
        monkeypatch.setattr(sensors, "read", read)
        monkeypatch.setattr(sensors, "members", members)
        monkeypatch.setattr(kgdb, "update_doc", update)
        monkeypatch.setattr(playbooks, "_fire", fire)


def _reading(value, **kw):
    return {"ok": True, "result": {"change_percent": value}, "source": "a.example", **kw}


@pytest.mark.parametrize("reading,kinds,fires,note", [
    (_reading(-6, tier="search", confidence="one page only"), ["search"], False, "unconfirmed"),
    (_reading(-6), ["api", "page"], False, "no second source"),
    (_reading(-6, agreement={"other_source": "b.example", "other_value": -1.0}), ["api", "page"], False, "but"),
    (_reading(-6, agreement={"other_source": "b.example", "other_value": -5.8}), ["api", "page"], True, None),
    (_reading(-6), ["api"], True, "single source"),
    (_reading(-6, tier="search", confidence="agreed by several pages"), ["search"], True, "single source"),
    ({"ok": False, "error": "every source failed"}, ["api"], False, "read failed"),
])
async def test_a_live_trigger_fires_only_when_a_second_source_agrees(monkeypatch, reading, kinds, fires, note):
    from core import playbooks
    pb = Playbooks(monkeypatch, reading, kinds)
    await playbooks.check_triggers(None, "d")
    assert bool(pb.fired) is fires
    assert (pb.notes[0] is None) if note is None else (note in pb.notes[0])


async def test_the_watcher_does_not_evaluate_beside_the_nightly_cycle(monkeypatch):
    from core import playbooks
    monkeypatch.setattr(playbooks, "load_domain", lambda d: {})
    playbooks._running.add("pbx")
    try:
        with pytest.raises(RuntimeError, match="running"):
            await playbooks.check_now(None, "pbx", "2026-01-01T00:00:00+00:00")
    finally:
        playbooks._running.discard("pbx")


async def test_a_trigger_without_a_live_reading_waits_for_its_meaning_check(monkeypatch):
    from core import playbooks, sensors
    asked = []

    async def groups(db, domain):
        return []

    async def pbs(db, domain, status="active"):
        return [{"uid": "p1", "situation": "s", "trigger": {"sensor": "q", "params": {}, "field": "price", "op": ">",
                                                             "value": 1}, "trigger_value": None}]

    async def meaning(*a):
        asked.append(a)
        return ""
    monkeypatch.setattr(sensors, "groups", groups)
    monkeypatch.setattr(playbooks, "list_playbooks", pbs)
    monkeypatch.setattr(playbooks, "_meaning", meaning)
    assert await playbooks.recheck_triggers(None, "d") == 0 and asked == []


async def test_the_strategist_cannot_duplicate_or_resurrect_a_playbook(monkeypatch):
    """A draft with no uid but an active playbook's name is that playbook; one bringing back what it retires in the
    same answer is dropped; trigger checks (a live read + a model call) only for drafts that are written."""
    from agents.strategist import PlaybookDraft, Playbooks as Answer, SensorTrigger
    from core import actions as actions_mod, playbooks, sensors
    current = [{"uid": "p_keep", "name": "HBM squeeze", "watch": [{"uid": "e_hbm", "name": "HBM"}]},
               {"uid": "p_old", "name": "Old idea", "watch": [{"uid": "e_old", "name": "Old"}]},
               {"uid": "p_held", "name": "Held one", "watch": [{"uid": "e_held", "name": "Held"}]}]
    trigger = SensorTrigger(sensor="q", field="price", op=">", value=1)
    answer = Answer(retire=["p_old"], playbooks=[
        PlaybookDraft(name="HBM Squeeze", situation="s", watch=["HBM"], response="r", action="alert", trigger=trigger),
        PlaybookDraft(name="Old idea again", situation="s", watch=["Old"], response="r", action="alert"),
        PlaybookDraft(uid="p_held", name="Held one", situation="s", watch=["Held"], response="r", action="alert",
                      trigger=trigger)])
    writes, checked = [], []

    async def returns(value):
        return value
    monkeypatch.setattr(playbooks, "load_domain", lambda d: {"config": {}})
    monkeypatch.setattr(actions_mod, "catalog", lambda db, d: returns([{"name": "alert"}]))
    monkeypatch.setattr(playbooks, "list_playbooks", lambda db, d: returns([dict(p) for p in current]))
    monkeypatch.setattr(sensors, "groups", lambda db, d: returns([]))
    monkeypatch.setattr(playbooks, "_context", lambda *a: returns(""))
    monkeypatch.setattr(playbooks, "write_playbooks", lambda ctx: returns(answer))
    monkeypatch.setattr(playbooks, "_moved", lambda db, d, pb: returns(pb["uid"] != "p_held"))
    monkeypatch.setattr(kgdb, "entities_by_keys", lambda db, d, keys: returns(
        [{"uid": f"e_{keys[0]}", "name": keys[0]}]))
    monkeypatch.setattr(kgdb, "get_facts", lambda db, d, uids: returns([]))
    monkeypatch.setattr(playbooks, "set_status", lambda db, d, uid, status: returns(writes.append(("retire", uid))))
    monkeypatch.setattr(kgdb, "update_doc", lambda db, d, t, uid, doc, *a, **kw: returns(writes.append(("update", uid))))
    monkeypatch.setattr(kgdb, "insert_doc", lambda db, d, t, doc, *a: returns(writes.append(("insert", doc["name"]))))
    monkeypatch.setattr(playbooks, "_check_trigger", lambda *a: returns(checked.append(a[-1]) or (None, "no")))
    monkeypatch.setattr(playbooks, "recheck_triggers", lambda db, d: returns(0))
    monkeypatch.setattr(playbooks, "_add_triggers", lambda db, d, s: returns(0))
    counts = await playbooks._refresh(None, "d")
    assert writes == [("retire", "p_old"), ("update", "p_keep")], writes
    assert counts["held"] == 1 and counts["created"] == 0 and len(checked) == 1
