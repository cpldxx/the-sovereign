"""The exam (core/exam.py): set from the charter and kept until it changes, graded from the graph's facts alone,
scored the same way every time, and its open questions handed to the learning cycle. The model and Graph RAG are
stood in; db tests use a throwaway database (conftest.scratch)."""

import pytest

from core import charter, exam, learning
from core.exam import CoreExam, ExamSet, Verdict

CHARTER = {"goal": "Know which treatments slow decline", "depth": "expert",
           "questions": ["Which antibodies cleared phase 3?", "What do blood tests show?"]}


def test_the_exam_is_set_again_only_when_the_charters_questions_or_depth_change():
    basis = exam._basis(CHARTER)
    assert exam._basis({**CHARTER, "goal": "something else", "scope": ["x"]}) == basis
    assert exam._basis({**CHARTER, "questions": CHARTER["questions"][:1]}) != basis
    assert exam._basis({**CHARTER, "depth": "overview"}) != basis


async def test_nothing_found_scores_nothing_without_asking_the_model():
    v = await exam._grade("d", "Which antibodies cleared phase 3?", [])
    assert v.points == 0 and v.facts == []


class _Agent:
    """Stands in for pydantic-ai: the exam it is given to set, then a verdict per question (by the question's text)."""
    verdicts: dict[str, Verdict] = {}
    asked: list[str] = []

    def __init__(self, *a, output_type, system_prompt, **kw):
        self.output_type, self.prompt = output_type, system_prompt

    def output_validator(self, fn):
        self.check = fn
        return fn

    async def run(self, prompt):
        _Agent.asked.append(prompt)
        if self.output_type is ExamSet:
            out = ExamSet(cores=[
                CoreExam(core=1, basic="Which antibody was approved first?", working="How much did lecanemab slow decline?",
                         deep="Which antibody was approved first of all?"),          # the basic in other words: dropped
                CoreExam(core=1, basic="A second set", working="for the same", deep="core question"),   # ignored
                CoreExam(core=2, basic="Which blood marker predicts amyloid?", working=" ", deep=""),   # blanks dropped
                CoreExam(core=9, basic="Out of range", working="no such", deep="core question"),
            ])
        else:
            question = prompt.split("\n")[0].removeprefix("QUESTION: ")
            out = _Agent.verdicts[question]
        return type("Run", (), {"output": self.check(out)})


def test_questions_asking_the_same_thing_in_other_words_are_alike():
    assert exam._alike("Which antibody was approved first?", "which antibody was approved first of all")
    assert not exam._alike("Which antibody was approved first?", "Which blood marker predicts amyloid?")
    assert exam._alike("HBM4 수율이 떨어지는 원인은?", "HBM4 수율이 떨어지는 원인은 무엇인가?")


async def test_a_korean_question_gets_a_korean_answer(monkeypatch):
    answers = iter([Verdict(points=2, answer="Aducanumab, in 2021.", facts=[1]),
                    Verdict(points=2, answer="2021년 아두카누맙.", facts=[1])])
    retries = []

    class Agent(_Agent):
        async def run(self, prompt):
            for v in answers:
                try:
                    return type("Run", (), {"output": self.check(v)})
                except exam.ModelRetry as e:
                    retries.append(str(e))
    monkeypatch.setattr(exam, "Agent", Agent)
    v = await exam._grade("d", "처음 승인된 항체는?", [{"fact": "Aducanumab was approved in 2021."}])
    assert v.answer == "2021년 아두카누맙." and retries == ["The question is in Korean: write the answer in Korean."]


async def test_an_answer_resting_on_no_fact_is_at_most_partial(monkeypatch):
    monkeypatch.setattr(exam, "Agent", _Agent)
    _Agent.verdicts = {"q": Verdict(points=2, answer="From memory.", facts=[7])}   # fact 7 doesn't exist
    v = await exam._grade("d", "q", [{"fact": "Something."}])
    assert v.points == 1 and v.facts == []


@pytest.fixture
def domain(scratch, monkeypatch):
    monkeypatch.setattr(charter.registry, "load_domain", lambda d: {"config": {"description": "Alzheimer's"}})
    monkeypatch.setattr(exam, "Agent", _Agent)
    facts = {"Which antibody was approved first?": [{"uid": "f1", "fact": "Aducanumab was approved in 2021.",
                                                      "sources": ["ep1", "ep2"], "evidence": 2}],
             "How much did lecanemab slow decline?": [{"uid": "f2", "fact": "Lecanemab was tested in Clarity AD.",
                                                       "sources": ["ep3"], "evidence": 1}],
             "Which blood marker predicts amyloid?": []}

    async def query(db, d, question, k):
        return {"facts": facts[question]}
    monkeypatch.setattr(exam.kg, "query", query)
    _Agent.verdicts = {"Which antibody was approved first?": Verdict(points=2, answer="Aducanumab, in 2021.", facts=[1]),
                       "How much did lecanemab slow decline?": Verdict(points=1, answer="Tested, no effect size.",
                                                                       facts=[1])}
    return scratch


@pytest.mark.db
async def test_a_sitting_is_graded_from_the_graph_and_scored(domain):
    db, d = domain
    with pytest.raises(exam.ExamError):
        await exam.take(db, d)                                            # no charter, no exam
    await charter.save(db, d, CHARTER)
    run = await exam.take(db, d)
    assert [x["question"] for x in run["items"]] == ["Which antibody was approved first?",
                                                     "How much did lecanemab slow decline?",
                                                     "Which blood marker predicts amyloid?"]
    assert [x["points"] for x in run["items"]] == [2, 1, 0]
    assert [x["tier"] for x in run["items"]] == ["basic", "working", "basic"]
    assert run["score"] == 50 and (run["answered"], run["partial"], run["open"], run["total"]) == (1, 1, 1, 3)
    assert [(c["score"], c["status"]) for c in run["cores"]] == [(75, "answered"), (0, "open")]
    first = run["items"][0]
    assert first["facts"] == ["f1"] and first["sources"] == 2 and first["well_sourced"]
    # The examiner saw only the facts the graph returned — never the charter's answers.
    assert any(p.startswith("QUESTION: Which antibody") and "[1] Aducanumab" in p for p in _Agent.asked)

    assert await exam.open_questions(db, d) == ["Which blood marker predicts amyloid?",
                                                "How much did lecanemab slow decline?"]
    assert "50/100" in await exam.line(db, d)


@pytest.mark.db
async def test_the_exam_stays_fixed_until_the_charter_changes(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    first = await exam.ensure(db, d)
    assert first["version"] == 1 and first["cores"] == CHARTER["questions"]
    await charter.save(db, d, {"goal": "A new goal"})
    assert (await exam.ensure(db, d))["version"] == 1                    # the questions didn't change
    await exam.take(db, d)
    assert (await exam.ensure(db, d, fresh=True))["version"] == 2         # new questions asked for: a new series
    await exam.take(db, d)
    await charter.save(db, d, {"questions": [*CHARTER["questions"], "Is there a vaccine?"]})
    assert (await exam.ensure(db, d))["version"] == 3
    # The open questions belong to the exam that is current: the old sitting's no longer count.
    assert await exam.open_questions(db, d) == []
    history = await exam.history(db, d)
    assert [h["version"] for h in history] == [1, 2] and history[0]["score"] == 50


@pytest.mark.db
async def test_the_scores_history_reads_oldest_first_and_the_briefing_shows_the_change(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.take(db, d)
    _Agent.verdicts["How much did lecanemab slow decline?"] = Verdict(points=2, answer="27% slower.", facts=[1])
    await exam.take(db, d)
    assert [h["score"] for h in await exam.history(db, d)] == [50, 67]
    assert "67/100 (was 50)" in await exam.line(db, d)


@pytest.mark.db
async def test_the_open_exam_questions_are_the_first_gaps(domain):
    db, d = domain
    await charter.save(db, d, CHARTER)
    await exam.take(db, d)
    g = await learning.gaps(db, d)
    assert g["exam"][0] == "Which blood marker predicts amyloid?"
    text = learning.gap_text(g)
    assert text.startswith("Exam questions") and "- Which blood marker predicts amyloid?" in text
