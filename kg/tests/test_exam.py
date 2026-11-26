"""The exam (core/exam.py): written from authoritative sources and kept only where the source states it, its sources
held out from research, graded against reference answers from the graph's facts alone, scored the same way every
time — and a practice set from other sources, whose open questions are handed to the learning cycle. The model, the
web and Graph RAG are stood in; db tests use a throwaway database (conftest.scratch)."""

import httpx
import pytest

from core import charter, exam, kg, learning
from core import database as kgdb
from core.exam import CoreExam, Grade, Pick, Plan, Verdict, Written

CHARTER = {"goal": "Know which treatments slow decline", "depth": "expert",
           "questions": ["Which drugs slow Alzheimer's decline?", "What do blood tests show?"]}
FDA = "https://www.fda.gov/news/leqembi"
FORUM = "https://forum.example/thread"
PAGE = ("The FDA approved Leqembi (lecanemab), an antibody against amyloid beta, in July 2023. In the Clarity AD trial "
        "it slowed decline by 27% over 18 months. ") * 10


def test_the_exam_is_set_again_only_when_the_charters_questions_or_depth_change():
    basis = exam._basis(CHARTER)
    assert exam._basis({**CHARTER, "goal": "something else", "scope": ["x"]}) == basis
    assert exam._basis({**CHARTER, "questions": CHARTER["questions"][:1]}) != basis
    assert exam._basis({**CHARTER, "depth": "overview"}) != basis


def test_questions_asking_the_same_thing_in_other_words_are_alike():
    assert exam._alike("Which antibody was approved first?", "which antibody was approved first of all")
    assert not exam._alike("Which antibody was approved first?", "Which blood marker predicts amyloid?")
    assert exam._alike("Γιατί πέφτει η απόδοση του HBM4;", "Γιατί πέφτει η απόδοση του HBM4 τελικά;")   # any script


async def test_nothing_found_scores_nothing_without_asking_the_model():
    v = await exam._grade("d", {"question": "Which drug was approved?", "answer": "Leqembi"}, [])
    assert v.points == 0 and v.facts == []


class _Agent:
    """Stands in for pydantic-ai by output type: the search plan, the pick, the questions written from a page, and a
    verdict per question (by its text). Every prompt it is given is kept."""
    verdicts: dict[str, Verdict] = {}
    prompts: list[tuple[str, str]] = []
    written = CoreExam(
        basic=Written(question="What kind of drug is Leqembi?", answer="An antibody against amyloid beta",
                      statement="Leqembi is an antibody against amyloid beta."),
        working=Written(question="How much did Leqembi slow decline in Clarity AD?", answer="27% over 18 months",
                        statement="In Clarity AD, Leqembi slowed decline by 27% over 18 months."),
        deep=Written(question="Why was Leqembi's approval disputed?", answer="Brain swelling risks",
                     statement="Leqembi's approval was disputed over brain swelling."))   # the page doesn't say it

    def __init__(self, *a, output_type, system_prompt, retries=1, **kw):
        self.output_type, self.system, self.retries = output_type, system_prompt, retries
        self.checks = []

    def output_validator(self, fn):
        self.checks.append(fn)
        return fn

    async def run(self, prompt):
        _Agent.prompts.append((self.system, prompt))
        if self.output_type is Plan:
            out = Plan(cores=[{"core": 1, "queries": ["alzheimer drugs review", "Alzheimer Medikamente"]},
                              {"core": 2, "queries": ["alzheimer blood test"]}])
        elif self.output_type is Pick:
            out = Pick(picks=[1, 2, 1])   # the first listed: the FDA's page (the forum, already read, is left out)
        elif self.output_type is CoreExam:
            out = _Agent.written
        else:   # the graph's answer, then its grade — each from the question's verdict
            v = _Agent.verdicts[prompt.split("\n")[0].removeprefix("QUESTION: ")]
            out = (v.answer + "".join(f" [{n}]" for n in v.facts)) if self.output_type is str else \
                Grade(points=v.points, conflict=v.conflict)
        for check in self.checks:
            out = check(out)
        return type("Run", (), {"output": out})


async def test_an_answer_resting_on_no_fact_earns_nothing(monkeypatch):
    monkeypatch.setattr(exam, "Agent", _Agent)
    _Agent.prompts = []
    _Agent.verdicts = {"q": Verdict(points=2, answer="From memory.", facts=[7])}   # fact 7 doesn't exist
    v = await exam._grade("d", {"question": "q", "answer": "a"}, [{"fact": "Something."}])
    assert v.points == 0 and v.facts == []
    assert len(_Agent.prompts) == 1                      # never graded: nothing to compare


async def test_an_answer_the_model_fails_to_shape_is_asked_once_more(monkeypatch):
    """One answer the model can't shape must not stop the exam: it is asked again, with more retries."""
    tries = []

    class Agent(_Agent):
        async def run(self, prompt):
            tries.append(self.retries)
            if len(tries) == 1:
                raise exam.UnexpectedModelBehavior("Exceeded maximum output retries (2)")
            return type("Run", (), {"output": "Leqembi is an antibody [1]."})
    monkeypatch.setattr(exam, "Agent", Agent)
    v = await exam._grade("d", {"question": "What is Leqembi?"}, [{"fact": "Leqembi is an antibody."}])
    assert v.answer == "Leqembi is an antibody." and v.facts == [1] and tries == [2, 4]


async def test_the_graph_is_answered_and_graded_at_temperature_zero(monkeypatch):
    """One graph scored 41, 23 and 36 at temperature 0.1 (2026-10-28): the same graph must get the same score."""
    temperatures = []

    class Agent(_Agent):
        def __init__(self, *a, model_settings, **kw):
            super().__init__(*a, **kw)
            temperatures.append(model_settings.get("temperature"))

        async def run(self, prompt):
            if "REFERENCE" not in prompt:
                return type("Run", (), {"output": "Leqembi is an antibody [1]."})
            return type("Run", (), {"output": Grade(points=2)})
    monkeypatch.setattr(exam, "Agent", Agent)
    v = await exam._grade("d", {"question": "What is Leqembi?", "answer": "An antibody"},
                          [{"fact": "Leqembi is an antibody."}])
    assert v.points == 2 and temperatures == [0.0, 0.0]          # the answer, then its grade


def test_the_graphs_answer_is_read_with_the_facts_it_cites():
    """The model answers in a sentence citing [n] — or with a "facts: [1, 8]" line (measured 2026-10-28)."""
    text = "HBM4 moves over 2 TB/s per stack through an interface twice as wide, 2,048 bits [1][8].\n\nfacts: [1, 8]"
    assert exam._cited(text, 15) == ("HBM4 moves over 2 TB/s per stack through an interface twice as wide, 2,048 bits.",
                                     [1, 8])
    assert exam._cited("Approved in 2023 [2][99].", 5) == ("Approved in 2023.", [2])   # no fact 99
    assert exam._cited("The facts don't say.", 5) == ("The facts don't say.", [])


@pytest.fixture
def domain(scratch, monkeypatch):
    monkeypatch.setattr(charter.registry, "load_domain", lambda d: {"config": {"description": "Alzheimer's"}})
    monkeypatch.setattr(exam, "Agent", _Agent)
    _Agent.prompts = []

    async def search(query):
        if "blood" in query:
            return []
        return [{"title": "Forum", "url": FORUM, "snippet": "my uncle says"},
                {"title": "FDA approves Leqembi", "url": FDA, "snippet": "FDA news release"}]

    async def fetch(url):
        return {"url": url, "title": "FDA approves Leqembi", "status": "ok", "markdown": PAGE}

    async def papers(query):
        return []

    async def validate(text, source, statements):
        return [type("V", (), {"supported": "disputed" not in s, "reliability": 0.9})() for s in statements]

    async def known_sources(db, d, urls):
        return [FORUM]                                   # already read: never an exam source

    graph = {"What kind of drug is Leqembi?": [{"uid": "f1", "fact": "Leqembi is an anti-amyloid antibody.",
                                                 "sources": ["ep1", "ep2"], "evidence": 2}],
             "How much did Leqembi slow decline in Clarity AD?": [{"uid": "f2", "fact": "Leqembi showed no benefit.",
                                                                    "sources": ["ep3"], "evidence": 1}]}

    async def query(db, d, question, k):
        return {"facts": graph.get(question, [])}
    monkeypatch.setattr(exam, "_search", search)
    monkeypatch.setattr(exam, "_fetch", fetch)
    monkeypatch.setattr(exam, "_papers", papers)
    monkeypatch.setattr(exam, "validate", validate)
    monkeypatch.setattr(exam.kg, "known_sources", known_sources)
    monkeypatch.setattr(exam.kg, "query", query)
    _Agent.verdicts = {
        "What kind of drug is Leqembi?": Verdict(points=2, answer="An anti-amyloid antibody.", facts=[1]),
        "How much did Leqembi slow decline in Clarity AD?": Verdict(points=0, conflict=True,
                                                                    answer="The graph says no benefit.", facts=[1])}
    return scratch


@pytest.mark.db
async def test_questions_are_written_from_a_source_and_kept_only_where_it_states_them(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    e = await exam.ensure(db, d)
    assert [(q["core"], q["tier"], q["question"]) for q in e["questions"]] == [
        (1, "basic", "What kind of drug is Leqembi?"), (1, "working", "How much did Leqembi slow decline in Clarity AD?")]
    assert e["questions"][1]["answer"] == "27% over 18 months" and e["questions"][0]["source"]["url"] == FDA
    assert e["unsourced"] == [2]                          # no source found for the blood tests
    # Pages the graph already read are not offered; the examiner wrote from the page picked — not from memory.
    listing = next(p for system, p in _Agent.prompts if system == exam.PICK)
    assert "[1] FDA approves Leqembi" in listing and "Forum" not in listing
    assert any("SOURCE (FDA approves Leqembi)" in prompt and "Clarity AD" in prompt for _, prompt in _Agent.prompts)
    # Its sources are held out: research skips them.
    assert await exam.held_out(db, d) == {FDA}


@pytest.mark.db
async def test_a_copy_of_a_page_the_graph_has_read_is_never_an_exam_source(domain, monkeypatch):
    """Exam sources must be new to the graph: a reprint of a page it read, under another address, is skipped like
    the page itself, and the next pick is read instead (until 2026-11-25 only the same URL counted, and went last)."""
    db, d = domain
    monkeypatch.setattr(exam, "PAGES", {"exam": 1, "practice": 2})
    article = " ".join(f"In cohort {i} lecanemab slowed decline by {i % 30} percent over 18 months." for i in range(80))
    await kgdb.create_episode(db, d, {"uid": "ep1", "source": "https://news.example/leqembi", "content": article,
                                      "content_hash": "ep1"}, status="done")

    async def search(query):
        return [] if "blood" in query else [{"title": "FDA approves Leqembi", "url": FDA, "snippet": "FDA"},
                                            {"title": "NIH: the Leqembi trial", "url": NIH, "snippet": "NIH"}]
    fetched = []

    async def fetch(url):
        fetched.append(url)
        text = f"FDA News Release\n{article}" if url == FDA else PAGE   # the FDA's page reprints the article read
        return {"url": url, "title": "x", "status": "ok", "markdown": text}
    monkeypatch.setattr(exam, "_search", search)
    monkeypatch.setattr(exam, "_fetch", fetch)
    await charter.save(db, d, CHARTER)
    e = await exam.ensure(db, d)
    assert fetched == [FDA, NIH] and {q["source"]["url"] for q in e["questions"]} == {NIH}


@pytest.mark.db
async def test_research_never_reads_a_copy_of_an_exam_source_under_another_address(domain, monkeypatch):
    """A reprint found after the exam was set would teach the graph its answers: the KG refuses research a near-duplicate
    of a held-out source (until 2026-11-26 research skipped only the source's own URL). Other pages are read."""
    db, d = domain
    article = " ".join(f"In cohort {i} lecanemab slowed decline by {i % 30} percent over 18 months." for i in range(80))

    async def fetch(url):
        return {"url": url, "title": "FDA approves Leqembi", "status": "ok", "markdown": article}
    monkeypatch.setattr(exam, "_fetch", fetch)
    await charter.save(db, d, CHARTER)
    await exam.ensure(db, d)
    held = await exam.held_sketches(db, d)
    assert list(held) == [FDA]
    monkeypatch.setattr(kg, "load_domain", lambda d: {"ontology": {"entity_types": ["entity"],
                                                                   "relation_types": ["relates_to"]}, "config": {}})

    async def read(*a, **k):
        return {"read": True}
    monkeypatch.setattr(kg, "_ingest", read)
    reprint = f"Health News Today\n{article}\nShare this story"
    assert await kg.ingest(db, d, reprint, "https://mirror.example/leqembi", held=held) == {"held_out": FDA}
    assert not await kg.read_pages(db, d)                                     # no episode: nothing of it was stored
    other = " ".join(f"In trial {i} donanemab cleared amyloid in {i % 40} of every 100 patients." for i in range(80))
    assert await kg.ingest(db, d, other, "https://other.example/donanemab", held=held) == {"read": True}  # same subject
    assert await kg.ingest(db, d, reprint, "user_input") == {"read": True}    # only research is held to it


@pytest.mark.db
async def test_an_exam_set_before_sketches_gets_them_at_its_next_sitting(domain, monkeypatch):
    """Its sources are read again once; a page that can't be read now is tried the next time."""
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.ensure(db, d)
    await exam._store(db, d, exam.SKETCHES, {})                               # as before 2026-11-26
    fetched = []

    async def fetch(url):
        fetched.append(url)
        if len(fetched) == 1:
            raise httpx.ConnectError("down")
        return {"url": url, "title": "FDA approves Leqembi", "status": "ok", "markdown": PAGE}
    monkeypatch.setattr(exam, "_fetch", fetch)
    await exam.ensure(db, d)
    assert fetched == [FDA] and not await exam.held_sketches(db, d)
    await exam.ensure(db, d)
    await exam.ensure(db, d)
    assert fetched == [FDA, FDA] and list(await exam.held_sketches(db, d)) == [FDA]


@pytest.mark.db
async def test_a_sitting_grades_the_graph_against_the_reference(domain):
    db, d = domain
    with pytest.raises(exam.ExamError):
        await exam.take(db, d)                            # no charter, no exam
    await charter.save(db, d, CHARTER)
    run = await exam.take(db, d)
    assert [(x["points"], x["conflict"]) for x in run["items"]] == [(2, False), (0, True)]
    assert run["items"][1]["reference"] == "27% over 18 months" and run["items"][0]["source"]["url"] == FDA
    assert (run["score"], run["answered"], run["open"], run["conflicts"], run["total"]) == (50, 1, 1, 1, 2)
    assert [(c["score"], c["status"]) for c in run["cores"]] == [(50, "partial"), (None, "unset")]
    first = run["items"][0]
    assert first["facts"] == ["f1"] and first["sources"] == 2 and first["well_sourced"]
    # The graph's answer was written from its facts without the reference; then graded against the reference, apart.
    answering = [p for system, p in _Agent.prompts if system == exam.ANSWER and p.startswith("QUESTION: How much")]
    assert answering and "[1] Leqembi showed" in answering[0] and "27%" not in answering[0]
    grading = [p for system, p in _Agent.prompts if system == exam.COMPARE and p.startswith("QUESTION: How much")]
    assert grading and "REFERENCE: 27% over 18 months" in grading[0] and "ANSWER: The graph says no benefit." in grading[0]
    assert await exam.open_questions(db, d) == []         # the exam's open questions never steer research
    assert "50/100" in await exam.line(db, d)


@pytest.mark.db
async def test_a_question_shown_the_same_facts_keeps_its_verdict(domain, monkeypatch):
    """Graded again only when its facts change: the score moves when the graph does, not with the model's dice."""
    db, d = domain
    await charter.save(db, d, CHARTER)
    first = await exam.take(db, d)
    asked = len(_Agent.prompts)
    _Agent.verdicts["What kind of drug is Leqembi?"] = Verdict(points=0, answer="Unclear.", facts=[1])   # other dice
    again = await exam.take(db, d)
    assert len(_Agent.prompts) == asked                                        # nothing asked of the model
    assert [(x["points"], x["answer"]) for x in again["items"]] == [(x["points"], x["answer"]) for x in first["items"]]
    assert again["items"][0]["facts"] == ["f1"] and again["items"][0]["well_sourced"]

    async def query(db_, d_, question, k):                                     # the graph learned the second answer
        if question.startswith("How much"):
            return {"facts": [{"uid": "f3", "fact": "Leqembi slowed decline by 27% over 18 months.", "sources": ["ep4"],
                               "evidence": 1}]}
        return {"facts": [{"uid": "f1", "fact": "Leqembi is an anti-amyloid antibody.", "sources": ["ep1", "ep2"],
                           "evidence": 2}]}
    monkeypatch.setattr(exam.kg, "query", query)
    _Agent.verdicts["How much did Leqembi slow decline in Clarity AD?"] = Verdict(points=2, answer="27%.", facts=[1])
    third = await exam.take(db, d)
    assert [x["points"] for x in third["items"]] == [2, 2]          # the first kept its verdict, the second graded anew


@pytest.mark.db
async def test_an_answer_written_as_before_keeps_its_grade(domain, monkeypatch):
    """Shown other facts, the model wrote the same answer: graded again, it could get 0 where it got 1 (2026-10-29)."""
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.take(db, d)

    async def query(db_, d_, question, k):                                     # one more fact, the same answers
        facts = [{"uid": "f1", "fact": "Leqembi is an anti-amyloid antibody.", "sources": ["ep1", "ep2"], "evidence": 2}]
        return {"facts": facts + [{"uid": "f9", "fact": "Leqembi is given by infusion.", "sources": ["ep9"]}]}
    monkeypatch.setattr(exam.kg, "query", query)
    _Agent.verdicts["What kind of drug is Leqembi?"] = Verdict(points=0, answer="An anti-amyloid antibody.", facts=[1])
    _Agent.prompts = []
    again = await exam.take(db, d)
    assert [x["points"] for x in again["items"]] == [2, 0]                     # the first kept its 2
    assert not [p for system, p in _Agent.prompts if system == exam.COMPARE and "What kind" in p]
    assert [p for system, p in _Agent.prompts if system == exam.ANSWER and "What kind" in p]   # answered anew


@pytest.mark.db
async def test_a_sitting_the_model_could_not_grade_is_not_scored(domain, monkeypatch):
    """2026-11-07: the model was killed for memory and a sitting of 30 "not graded (ConnectError)" stood as 0/100."""
    db, d = domain
    await charter.save(db, d, CHARTER)
    first = await exam.take(db, d)

    async def unreachable(*a, **k):
        raise httpx.ConnectError("All connection attempts failed")
    monkeypatch.setattr(exam.kg, "query", unreachable)
    with pytest.raises(exam.ExamError, match="called off"):
        await exam.take(db, d)
    assert (await exam.latest(db, d))["uid"] == first["uid"]           # the last real sitting still stands


@pytest.mark.db
async def test_the_exam_stays_fixed_until_the_charter_changes_or_a_question_is_dropped(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    assert (await exam.ensure(db, d))["version"] == 1
    await charter.save(db, d, {"goal": "A new goal"})
    assert (await exam.ensure(db, d))["version"] == 1                    # the questions didn't change
    await exam.take(db, d)
    assert (await exam.ensure(db, d, fresh=True))["version"] == 2         # new questions asked for
    dropped = await exam.remove(db, d, "q1")
    assert dropped["version"] == 3 and [q["id"] for q in dropped["questions"]] == ["q2"]
    with pytest.raises(exam.ExamError):
        await exam.remove(db, d, "q2")                                    # never the last one
    await charter.save(db, d, {"questions": [*CHARTER["questions"], "Is there a vaccine?"]})
    assert (await exam.ensure(db, d))["version"] == 4
    assert await exam.open_questions(db, d) == []                         # the old sitting's no longer count
    assert [h["version"] for h in await exam.history(db, d)] == [1]


@pytest.mark.db
async def test_an_exam_written_from_memory_is_set_again_from_sources(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam._save(db, d, {"version": 3, "basis": exam._basis(CHARTER), "created_at": "",
                             "cores": CHARTER["questions"],
                             "questions": [{"id": "q1", "core": 1, "question": "From memory?"}]})
    e = await exam.ensure(db, d)
    assert e["version"] == 4 and e["grounded"] == exam.GROUNDED and e["questions"][0]["source"]


@pytest.mark.db
async def test_the_scores_history_reads_oldest_first_and_the_briefing_shows_the_change(domain, monkeypatch):
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.take(db, d)
    before = exam.kg.query

    async def query(db_, d_, question, k):                       # the graph learned the second answer
        if question.startswith("How much"):
            return {"facts": [{"uid": "f3", "fact": "Leqembi slowed decline by 27%.", "sources": ["ep4"]}]}
        return await before(db_, d_, question, k)
    monkeypatch.setattr(exam.kg, "query", query)
    _Agent.verdicts["How much did Leqembi slow decline in Clarity AD?"] = Verdict(points=2, answer="27%.", facts=[1])
    await exam.take(db, d)
    assert [h["score"] for h in await exam.history(db, d)] == [50, 100]
    assert "100/100 (was 50)" in await exam.line(db, d)


NIH = "https://www.nih.gov/news/leqembi-trial"
PRACTICE = CoreExam(
    basic=Written(question="Which trial tested Leqembi?", answer="Clarity AD",
                  statement="Leqembi was tested in the Clarity AD trial."),
    working=Written(question="When was Leqembi approved?", answer="July 2023",
                    statement="The FDA approved Leqembi in July 2023."),
    deep=Written(question="Why is Leqembi disputed?", answer="Swelling", statement="Leqembi is disputed."))
CDC = "https://www.cdc.gov/alzheimers/leqembi"
PRACTICE_2 = CoreExam(
    basic=Written(question="Who can take Leqembi?", answer="People with early Alzheimer's",
                  statement="Leqembi is for people with early Alzheimer's."),
    working=Written(question="How is Leqembi given?", answer="By infusion", statement="Leqembi is given by infusion."),
    deep=Written(question="Why is Leqembi still disputed?", answer="Swelling", statement="It is disputed."))


@pytest.mark.db
async def test_practice_comes_from_other_sources_and_only_its_open_questions_steer_research(domain, monkeypatch):
    """Research aimed at the exam's open questions, and changes tried on its answers, taught to the test (2026-10-29)."""
    db, d = domain
    monkeypatch.setattr(exam, "PAGES", {"exam": 1, "practice": 2})   # one page each set here: three sources in all

    async def search(query):
        return [] if "blood" in query else [{"title": "Forum", "url": FORUM, "snippet": "my uncle says"},
                                            {"title": "FDA approves Leqembi", "url": FDA, "snippet": "FDA"},
                                            {"title": "NIH: the Leqembi trial", "url": NIH, "snippet": "NIH"},
                                            {"title": "CDC: Leqembi", "url": CDC, "snippet": "CDC"}]

    async def fetch(url):
        title = {NIH: "NIH: the Leqembi trial", CDC: "CDC: Leqembi"}.get(url, "FDA approves Leqembi")
        return {"url": url, "title": title, "status": "ok", "markdown": PAGE}

    class Agent(_Agent):
        async def run(self, prompt):
            if self.output_type is CoreExam and "SOURCE (NIH" in prompt:
                return type("Run", (), {"output": PRACTICE})
            if self.output_type is CoreExam and "SOURCE (CDC" in prompt:
                return type("Run", (), {"output": PRACTICE_2})
            return await super().run(prompt)
    monkeypatch.setattr(exam, "_search", search)
    monkeypatch.setattr(exam, "_fetch", fetch)
    monkeypatch.setattr(exam, "Agent", Agent)
    await charter.save(db, d, CHARTER)
    assert {q["source"]["url"] for q in (await exam.ensure(db, d))["questions"]} == {FDA}
    practice = await exam.ensure(db, d, kind="practice")
    assert [(q["question"], q["source"]["url"]) for q in practice["questions"]] == [   # two pages, never the exam's
        ("Which trial tested Leqembi?", NIH), ("When was Leqembi approved?", NIH),
        ("Who can take Leqembi?", CDC), ("How is Leqembi given?", CDC)]
    assert await exam.held_out(db, d) == {FDA, NIH, CDC}                                  # research skips them all
    await exam.take(db, d)
    await exam.take(db, d, kind="practice")
    g = await learning.gaps(db, d)
    assert g["practice"] == [q["question"] for q in practice["questions"]]
    assert learning.gap_text(g).startswith("Practice questions")
    assert (await exam.latest(db, d))["items"][1]["question"].startswith("How much")    # each set its own sittings
    assert [len(await exam.history(db, d)), len(await exam.history(db, d, kind="practice"))] == [1, 1]


@pytest.mark.db
async def test_a_question_that_cant_be_graded_scores_nothing_and_the_sitting_goes_on(domain, monkeypatch):
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.ensure(db, d)

    async def query(db_, d_, question, k):
        if question.startswith("What kind"):
            raise RuntimeError("embedding service down")
        return {"facts": []}
    monkeypatch.setattr(exam.kg, "query", query)
    run = await exam.take(db, d)
    assert [x["points"] for x in run["items"]] == [0, 0] and run["items"][0]["answer"].startswith("Not graded")
    assert run["items"][1]["answer"] == "The graph holds nothing on this yet."


@pytest.mark.db
async def test_official_sources_are_offered_before_other_sites_and_blogs_never(domain, monkeypatch):
    db, d = domain

    async def search(query):
        return [{"title": "My take", "url": "https://medium.com/@someone/leqembi", "snippet": "I think"},
                {"title": "Leqembi explained", "url": "https://pharma-news.example/leqembi", "snippet": "a site"},
                {"title": "FDA approves Leqembi", "url": FDA, "snippet": "FDA news release"}]
    monkeypatch.setattr(exam, "_search", search)
    await charter.save(db, d, CHARTER)
    await exam.ensure(db, d)
    listing = next(p for system, p in _Agent.prompts if system == exam.PICK)
    assert "[1] FDA approves Leqembi [official]" in listing and "[2] Leqembi explained [web]" in listing
    assert "medium.com" not in listing


@pytest.mark.db
async def test_a_paper_comes_first_and_its_abstract_is_the_source(domain, monkeypatch):
    db, d = domain
    monkeypatch.setattr(exam, "PAGES", {"exam": 1, "practice": 2})   # the first pick is the one page read
    paper = {"title": "Lecanemab in Early Alzheimer's Disease", "url": "https://doi.org/10.1056/NEJMoa2212948",
             "venue": "NEJM", "year": 2023, "abstract": "Lecanemab reduced amyloid…", "markdown": PAGE}
    fetched = []

    async def papers(query):
        return [paper] if "alzheimer" in query else []

    async def fetch(url):
        fetched.append(url)
        return {"url": url, "title": "x", "status": "ok", "markdown": PAGE}
    monkeypatch.setattr(exam, "_papers", papers)
    monkeypatch.setattr(exam, "_fetch", fetch)
    await charter.save(db, d, CHARTER)
    e = await exam.ensure(db, d)
    listing = next(p for system, p in _Agent.prompts if system == exam.PICK)
    assert listing.startswith("QUESTION") and "[1] Lecanemab in Early Alzheimer's Disease [paper · NEJM · 2023]" in listing
    assert e["questions"][0]["source"]["url"] == paper["url"] and fetched == []   # picked first, never fetched
    assert paper["url"] in await exam.held_out(db, d)                              # held out like any source
