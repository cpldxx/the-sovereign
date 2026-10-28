"""The research charter (core/charter.py): what is accepted, how a draft becomes active and starts the first deep
research, how the Head's suggestions grow it, and what research prompts see. db tests use a throwaway database
(conftest.scratch) with the domain's description stood in."""

import pytest

from core import charter
from core.charter import CharterError, check

DESCRIPTION = "Alzheimer's drug research: antibodies, trials, biomarkers"
CHARTER = {"goal": "Know which treatments slow decline", "questions": ["Which antibodies cleared phase 3?"],
           "scope": ["clinical trials since 2020"], "exclude": ["supplements"], "depth": "expert"}


@pytest.fixture
def domain(scratch, monkeypatch):
    monkeypatch.setattr(charter.registry, "load_domain", lambda d: {"config": {"description": DESCRIPTION}})
    return scratch


def test_edits_are_cleaned_and_checked():
    assert check({"questions": ["  Which  antibodies? ", "Which antibodies?", ""], "depth": "expert"}) == \
        {"questions": ["Which antibodies?"], "depth": "expert"}
    assert check({"exclude": "supplements"}) == {"exclude": ["supplements"]}
    for bad in ({"depth": "phd"}, {"goal": "x" * 601}, {"questions": [f"q{i}" for i in range(13)]},
                {"scope": ["x" * 301]}, {"questions": 3}):
        with pytest.raises(CharterError):
            check(bad)


def test_a_models_charter_is_cut_to_fit_not_refused():
    fitted = charter._fit({"goal": "g " * 400, "questions": [f"q{i}" for i in range(20)] + ["q1"], "depth": "phd"})
    assert len(fitted["goal"]) == charter.LIMITS["goal"]
    assert fitted["questions"] == [f"q{i}" for i in range(12)]
    assert fitted["depth"] == "working" and fitted["scope"] == [] and fitted["exclude"] == []


def test_prompts_read_every_part():
    text = charter.text(CHARTER)
    for part in ("Goal: Know which", "- Which antibodies cleared phase 3?", "In scope: clinical trials since 2020",
                 "Leave out: supplements", "Depth: expert — specialist depth"):
        assert part in text
    assert charter.text(None) == ""


@pytest.mark.db
async def test_a_draft_steers_nothing_until_confirmed_then_steers_for_good(domain):
    db, d = domain
    assert (await charter.get(db, d))["status"] == "none"
    with pytest.raises(CharterError):
        await charter.confirm(db, d)                                      # nothing to confirm yet
    with pytest.raises(CharterError):
        await charter.save(db, d, {"goal": "only a goal"})                # a charter needs a question
    assert (await charter.save(db, d, CHARTER))["status"] == "draft"
    assert await charter.brief(db, d) == DESCRIPTION                     # a draft steers nothing yet
    state, first = await charter.confirm(db, d)
    assert state["status"] == "active" and first and state["confirmed_at"]
    assert not (await charter.confirm(db, d))[1]                         # once: the first research starts once
    brief = await charter.brief(db, d)
    assert brief.startswith(DESCRIPTION) and "Which antibodies cleared phase 3?" in brief and "supplements" in brief
    assert (await charter.save(db, d, {"depth": "overview"}))["status"] == "active"   # an edit steers at once
    assert (await charter.reset(db, d))["status"] == "none"
    assert await charter.brief(db, d) == DESCRIPTION


@pytest.mark.db
async def test_the_heads_suggestions_wait_for_the_user(domain):
    db, d = domain
    with pytest.raises(CharterError):
        await charter.suggest(db, d, "question", "Is there a vaccine?")    # no charter yet
    await charter.save(db, d, CHARTER)
    await charter.suggest(db, d, "question", "Is there a vaccine?", "asked twice")
    await charter.suggest(db, d, "question", "is there a vaccine?")        # waits already
    await charter.suggest(db, d, "question", "which antibodies cleared phase 3?")   # the charter has it
    state = await charter.suggest(db, d, "exclude", "animal studies")
    assert [(s["kind"], s["text"]) for s in state["suggestions"]] == \
        [("question", "Is there a vaccine?"), ("exclude", "animal studies")]
    with pytest.raises(CharterError):
        await charter.suggest(db, d, "goal", "something")
    vaccine, animal = state["suggestions"]
    state = await charter.decide(db, d, vaccine["uid"], accept=True)
    state = await charter.decide(db, d, animal["uid"], accept=False)
    assert state["charter"]["questions"][-1] == "Is there a vaccine?"
    assert state["charter"]["exclude"] == ["supplements"] and state["suggestions"] == []
    with pytest.raises(CharterError):
        await charter.decide(db, d, vaccine["uid"], accept=True)         # decided once


@pytest.mark.db
async def test_a_draft_comes_from_the_interview_and_a_revision_stays_active(domain, monkeypatch):
    db, d = domain
    seen = []

    class Written:
        def __init__(self, output):
            self.output = output

    class Agent:
        def __init__(self, *a, system_prompt, **kw):
            self.prompt = system_prompt

        def output_validator(self, fn):
            self.check = fn
            return fn

        async def run(self, notes):
            seen.append((self.prompt, notes))
            return Written(self.check(charter.Charter(goal="Know which treatments slow decline",
                                                      questions=["Which antibodies cleared phase 3?", ""], depth="expert")))
    monkeypatch.setattr(charter, "Agent", Agent)
    state = await charter.draft(db, d, "I care for my father; I want to know what slows decline")
    assert state["status"] == "draft" and state["charter"]["questions"] == ["Which antibodies cleared phase 3?"]
    assert DESCRIPTION in seen[0][0] and "my father" in seen[0][1]
    await charter.confirm(db, d)
    state = await charter.draft(db, d, "also blood tests")
    assert state["status"] == "active"                                     # a revision steers at once
    assert "The current charter" in seen[1][0] and "Which antibodies" in seen[1][0]


@pytest.mark.db
async def test_a_korean_charter_with_chinese_characters_is_written_again(domain, monkeypatch):
    db, d = domain
    drafts = [charter.Charter(goal="각社의 기술을 알고 싶다", questions=["일반적인半导体 역사는?"]),
              charter.Charter(goal="각 회사의 기술을 알고 싶다", questions=["반도체 역사는?"])]
    retries = []

    class Agent:
        def __init__(self, *a, **kw):
            pass

        def output_validator(self, fn):
            self.check = fn
            return fn

        async def run(self, notes):
            for c in drafts:                       # pydantic-ai asks again when the validator says ModelRetry
                try:
                    return type("Run", (), {"output": self.check(c)})
                except charter.ModelRetry as e:
                    retries.append(str(e))
    monkeypatch.setattr(charter, "Agent", Agent)
    state = await charter.draft(db, d, "회사별 기술 차이가 궁금해")
    assert state["charter"]["goal"] == "각 회사의 기술을 알고 싶다"
    assert len(retries) == 1 and "社" in retries[0] and "半" in retries[0]


@pytest.mark.db
async def test_confirming_starts_the_first_deep_research_once(domain, monkeypatch):
    db, d = domain
    posted = []

    class Answer:
        def __init__(self, ok=True):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise charter.httpx.HTTPStatusError("down", request=None, response=None)

        def json(self):
            return {"jobs": [{"id": "job_1", "mode": "bootstrap"}]}

    class Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, path, json):
            posted.append((path, json))
            return Answer(ok=len(posted) == 1)
    monkeypatch.setattr(charter.httpx, "AsyncClient", Client)
    monkeypatch.setattr(charter, "KICKOFF", 2)
    await charter.save(db, d, {**CHARTER, "questions": ["q one", "q two", "q three"]})
    state = await charter.start(db, d)
    assert state["status"] == "active" and state["kickoff"]["jobs"][0]["mode"] == "bootstrap"
    assert posted == [(f"/domains/{d}/kickoff", {"questions": ["q one", "q two"]})]
    assert (await charter.start(db, d))["kickoff"] is None                # already active: nothing new
    assert len(posted) == 1

    await charter.reset(db, d)
    await charter.save(db, d, CHARTER)
    state = await charter.start(db, d)                                     # the research service says no
    assert state["status"] == "active" and "next learning cycle" in state["kickoff"]["error"]
