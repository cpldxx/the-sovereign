"""The learning cycle (core/learning.py): when a cycle is due, and what it reads as gaps. No database: stand-ins."""

from datetime import datetime, timezone

from core import learning
from core.learning import LearningError, check, due


def test_a_learning_cycle_is_due_once_per_slot_in_the_users_timezone():
    s = {"enabled": True, "times": ["03:00", "15:00"], "timezone": "America/New_York"}
    at = datetime(2026, 10, 13, 7, 10, tzinfo=timezone.utc)          # 03:10 in New York
    assert due(s, at, set()) == "2026-10-13 03:00"
    assert due(s, at, {"2026-10-13 03:00"}) is None                     # ran already
    assert due(s, datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc), set()) is None   # 04:00: window closed
    assert due({**s, "enabled": False}, at, set()) is None
    for bad in ({"times": ["9:00"]}, {"times": ["25:00"]}, {"timezone": "Mars/Base"}, {"curiosity": 9}):
        try:
            check(bad)
            raise AssertionError(bad)
        except LearningError:
            pass
    assert check({"times": ["15:00", "03:00", "03:00"]}) == {"times": ["03:00", "15:00"]}


def test_a_window_runs_past_midnight_and_keeps_the_day_it_started():
    s = {"enabled": True, "times": ["23:59"], "timezone": "UTC"}
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)                  # noqa: E731
    assert due(s, utc(2026, 10, 13, 23, 59), set()) == "2026-10-13 23:59"
    assert due(s, utc(2026, 10, 14, 0, 20), set()) == "2026-10-13 23:59"     # 21 minutes in, the next day
    assert due(s, utc(2026, 10, 14, 0, 20), {"2026-10-13 23:59"}) is None
    assert due(s, utc(2026, 10, 14, 0, 29), set()) is None                   # 30 minutes: closed
    late = {**s, "times": ["23:45"]}
    assert due(late, utc(2026, 10, 14, 0, 10), set()) == "2026-10-13 23:45"  # a full 30 minutes, not 14
    assert due(late, utc(2026, 10, 14, 0, 15), set()) is None
    assert due(late, utc(2026, 10, 13, 23, 44), set()) is None


class _DB:
    async def exists(self, domain):
        return True

    async def cypher(self, domain, query, **params):
        return []


async def test_a_question_is_paired_with_its_own_askers_answer(monkeypatch):
    web = [{"name": "web_search"}]
    turns = [{"user": "ann", "role": "user", "content": "Ann asks about HBM", "n": 1},
             {"user": "bob", "role": "user", "content": "Bob asks about CoWoS", "n": 2},
             {"user": "ann", "role": "assistant", "content": "…", "steps": web, "n": 3},     # answers Ann
             {"user": "bob", "role": "assistant", "content": "…", "steps": [], "n": 4},      # from the graph
             {"user": "ann", "role": "assistant", "content": "…", "steps": web, "n": 5}]     # no question before it

    async def find_docs(db, domain, type_, *a, **k):
        return [dict(t) for t in turns] if type_ == "ChatTurn" else []
    monkeypatch.setattr(learning.kgdb, "find_docs", find_docs)
    g = await learning.gaps(_DB(), "d")
    assert g["unanswered"] == ["Ann asks about HBM"]
    assert g["unanswered_total"] == 1
    # A person sees only their own unanswered questions; everyone's still feed the Strategist (user=None).
    assert (await learning.gaps(_DB(), "d", "bob"))["unanswered"] == []
    assert (await learning.gaps(_DB(), "d", "ann"))["unanswered"] == ["Ann asks about HBM"]
