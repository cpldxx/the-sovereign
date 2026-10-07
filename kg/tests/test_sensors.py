"""Sensor code rules, the group shape, routing between sources, and the data-driven agreement (no services)."""

import pytest

from core import sensors
from core.sensors import conform, outliers, relative_spread

MODULE = '''import re
NAME = "quote"
DESCRIPTION = "x"
PARAMS = {"symbol": {"type": "string", "description": "t", "example": "NVDA"}}
%s
def run(symbol: str) -> dict:
    return {}
'''


@pytest.mark.parametrize("line,problem", [
    ("import os", "import os is not allowed"),
    ("import subprocess", "import subprocess is not allowed"),
    ("from socket import socket", "from socket import"),
    ("x = eval('1')", "eval is not allowed"),
    ("x = getattr(re, 'compile')", "getattr is not allowed"),
    ("x = open('/etc/passwd')", "open is not allowed"),
    ("x = re.__dict__", "dunder attribute"),
    ("x = __import__('os')", "__import__ is not allowed"),
    ('PAGES = {"main": "file:///etc/passwd"}', "must be an http(s) URL"),
    ('PAGES = {"main": "https://x.com/{ticker}"}', "is not a parameter"),
])
def test_static_check_refuses(line, problem):
    _, problems = sensors.inspect_code(MODULE % line)
    assert any(problem in p for p in problems), problems


def test_static_check_accepts_pages():
    meta, problems = sensors.inspect_code(MODULE % 'PAGES = {"main": "https://x.com/q/{symbol|lower}"}')
    assert problems == [] and meta["pages"]["main"].endswith("{symbol|lower}")


def test_fill_encodes_and_changes_case():
    assert sensors.fill("https://a.com/{symbol}?q={symbol|lower}", {"symbol": "BRK B/1"}) == \
        "https://a.com/BRK%20B%2F1?q=brk%20b%2F1"


SCHEMA = [{"name": "price", "type": "number", "required": True},
          {"name": "change_percent", "type": "number", "required": False},
          {"name": "name", "type": "string", "required": False}]


def test_conform_parses_numbers_and_drops_extras():
    data, problem = conform({"price": "$1,236.95", "change_percent": "−0.94%", "junk": 1, "source": "u"}, SCHEMA)
    assert problem == "" and data == {"price": 1236.95, "change_percent": -0.94, "source": "u"}


def test_conform_drops_a_bad_optional_field_but_fails_a_bad_required_one():
    data, _ = conform({"price": 237.0, "name": "navigation-container) [Skip to main content ]"}, SCHEMA)
    assert data == {"price": 237.0}
    assert conform({"price": "n/a"}, SCHEMA)[0] is None
    assert conform({"change_percent": 1.0}, SCHEMA)[0] is None
    assert conform([1, 2], SCHEMA)[0] is None


def test_outliers_from_the_data():
    prices = [236.9, 237.0, 236.95, 239.24, 237.05, 236.97]          # a previous close among live prices
    assert outliers(prices) == [False, False, False, True, False, False]
    assert relative_spread(prices) < 0.001
    assert relative_spread([-0.3, -0.52, -0.74, -0.6]) > 0.05       # after hours: spread out by nature
    assert sensors.tolerance({}) == sensors.AGREE
    assert sensors.tolerance({"spread": 0.0003}) == 0.002 and sensors.tolerance({"spread": 0.5}) == 0.05


def test_route_orders_sources():
    key = sensors._params_key({"symbol": "TSM"})
    sources = [{"name": "missed-tsm", "misses": {key: sensors.now()}},
               {"name": "ok"},
               {"name": "robots", "cooldown_until": "2999-01-01T00:00:00+00:00", "last_error": "robots.txt disallows"},
               {"name": "blocked", "cooldown_until": "2999-01-01T00:00:00+00:00", "last_error": "HTTP 403"}]
    assert [m["name"] for m in sensors._route(sources, {"symbol": "TSM"})] == ["ok", "missed-tsm", "blocked"]
    assert [m["name"] for m in sensors._route(sources, {"symbol": "AMD"})] == ["missed-tsm", "ok", "blocked"]


@pytest.mark.parametrize("error,blocked", [
    ("crawler HTTP 502: Blocked by anti-bot protection: HTTP 403", True),
    ("PermissionError: robots.txt of x disallows y", True),
    ("HTTPStatusError: 429 Too Many Requests", True),
    ("ValueError: price not found on the page", False),
])
def test_refusals_are_recognised(error, blocked):
    assert bool(sensors._BLOCKED.search(error)) is blocked


def test_samples_stay_valid_json():
    import json
    big = {"items": [{"title": "x" * 500}] * 50, "n": 3}
    assert json.loads(json.dumps(sensors.shrink(big)))["items"][0]["title"] == "x" * 200
    cut = '{"source": "x", "items": [{"title": "a", "link": "b"}, {"title": "c'
    assert sensors.sample_of({"sample": cut}) == {"source": None, "items": None}
