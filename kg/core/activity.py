"""Live activity for the agent view: which agent is working on what, and who just handed what to whom.

    working(domain, agent, what)          while it runs, the agent shows as busy with `what`
    event(domain, source, target, kind)   one hand-off: a task, a report, a tool call, facts written to the graph

Agents are the crew's names plus "head" and "graph" (the knowledge graph itself). Kept in memory, per domain, for the
last EVENTS hand-offs: a picture of now, not a log — threads, reports and reviews keep the history. Polled by the UI
(GET /domains/{domain}/activity?after=<seq>).
"""

import itertools
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone

EVENTS = 300
_seq = itertools.count(1)
_last = 0
_events: defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=EVENTS))
_working: defaultdict[str, dict[int, dict]] = defaultdict(dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(domain: str, source: str, target: str, kind: str, text: str = "") -> None:
    global _last
    _last = next(_seq)
    _events[domain].append({"seq": _last, "at": _now(), "source": source, "target": target, "kind": kind,
                            "text": text[:140]})


@contextmanager
def working(domain: str, agent: str, what: str = ""):
    token = next(_seq)
    _working[domain][token] = {"agent": agent, "what": what[:140], "since": _now(), "t": time.monotonic()}
    try:
        yield
    finally:
        _working[domain].pop(token, None)


def snapshot(domain: str, after: int = 0) -> dict:
    """What is going on now: busy agents (one entry per agent, with everything it is doing) and the hand-offs
    after `after`."""
    busy: dict[str, dict] = {}
    for w in sorted(_working[domain].values(), key=lambda w: w["t"]):
        b = busy.setdefault(w["agent"], {"agent": w["agent"], "since": w["since"], "doing": []})
        if w["what"] and w["what"] not in b["doing"]:
            b["doing"].append(w["what"])
    return {"seq": _last, "working": list(busy.values()),
            "events": [e for e in _events[domain] if e["seq"] > after]}
