"""The Head's team: tasks out on the queue, reports back on threads, the Head woken within its guard.

Runs on a throwaway domain in a temporary domains directory (the real domains are never touched); agents are
stand-ins, so no model is needed — the real roster is checked for shape only."""

import asyncio

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
    domain = registry.create_domain("Crew test", "A throwaway domain for the crew tests")
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
