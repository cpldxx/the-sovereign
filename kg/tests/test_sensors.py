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


def test_model_read_values_must_be_quoted_from_the_page():
    page = "London weather now\nTemperature 18°C, feels like 16°C\nHumidity 72%\nUpdated 14:05"
    schema = [{"name": "temperature", "type": "number", "required": True},
              {"name": "humidity", "type": "number", "required": False},
              {"name": "conditions", "type": "string", "required": False}]
    values = {"temperature": 18, "humidity": 75, "conditions": "Sunny"}
    evidence = {"temperature": "Temperature 18°C", "humidity": "Humidity 72%", "conditions": "sunny skies"}
    # 18 is in its quote and the quote is on the page; 75 isn't in its quote; "sunny skies" isn't on the page.
    assert sensors.verified(values, evidence, page, schema) == {"temperature": 18.0}
    assert sensors.verified({"temperature": 18}, {}, page, schema) == {}                # no quote, no value


def test_excerpt_keeps_the_passages_about_the_parameters():
    text = "nav " * 3000 + "Seoul 21°C clear" + " footer" * 3000
    part = sensors.excerpt(text, {"city": "Seoul"})
    assert "Seoul 21°C clear" in part and len(part) <= sensors.EXCERPT


@pytest.mark.parametrize("url,bad", [
    ("http://api.openweathermap.org/data/2.5/weather?id=1850147&appid=2370a9749f38195f07d3bbefd145b74c", True),
    ("https://samples.openweathermap.org/data/2.5/weather?q=London", True),
    ("https://example.com/q?apikey=abcdef123456", True),
    ("https://api.example.com/v1/price?symbol=NVDA&token=deadbeef99", True),
    ("https://www.cnbc.com/quotes/NVDA", False),
    ("https://finance.yahoo.com/quote/NVDA/?p=NVDA", False),
])
def test_addresses_with_someone_elses_key_or_demo_data_are_never_read(url, bad):
    assert bool(sensors.unusable(url)) is bad


def test_a_module_with_a_key_written_in_is_refused():
    code = MODULE % 'URL = "http://api.openweathermap.org/data/2.5/weather?q=x&appid=2370a9749f38195f07d3bbefd145b74c"'
    assert any("credential" in p for p in sensors.inspect_code(code)[1])


# ── 2026-10-14 review fixes ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    'x = __builtins__["__import__"]("socket")',          # the real importer by name
    'x = __loader__',
    'import io\nx = io.open("/etc/passwd")',             # a file opener reached as an attribute
    'import io\nx = io.FileIO("/etc/passwd")',
    'x = re._compile',                                   # private internals (httpx._utils.os)
    'x = d["__class__"]',
    'x = super()',
    'x = type(re).mro()',                                # the C socket class, past the egress guard
    'x = r.extensions["network_stream"].get_extra_info("socket")',
])
def test_static_check_refuses_ways_around_it(line):
    assert sensors.inspect_code(MODULE % line)[1], line


def test_static_check_keeps_ordinary_code():
    for line in ('if __name__ == "__main__":\n    pass', 'x = {"open": 1}["open"]', 'x = {"_id": 1}["_id"]'):
        assert sensors.inspect_code(MODULE % line)[1] == [], line


def test_runner_allows_the_same_imports_as_the_static_check():
    import ast
    tree = ast.parse((sensors.SANDBOX_DIR / "runner.py").read_text())
    allowed = next(ast.literal_eval(n.value) for n in tree.body
                   if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ALLOWED")
    assert allowed == sensors.ALLOWED_IMPORTS


def test_runner_lets_stdlib_c_code_import_what_it_needs():
    """datetime.strptime imports _strptime through the calling frame's __import__ — the sensor's curated one. The
    runner itself (no container: this checks its code, the image is built from the same file)."""
    import json
    import subprocess
    import sys
    code = ('import time\nfrom datetime import datetime\nNAME = "t"\nDESCRIPTION = "x"\nPARAMS = {}\n'
            'def run():\n    return {"d": datetime.strptime("2026-10-14", "%Y-%m-%d").day,\n'
            '            "t": time.strptime("2026", "%Y").tm_year}')
    out = subprocess.run([sys.executable, str(sensors.SANDBOX_DIR / "runner.py")], capture_output=True, timeout=60,
                         input=json.dumps({"code": code, "params": {}}).encode())
    assert json.loads(out.stdout.decode().strip().splitlines()[-1]) == {"ok": True, "result": {"d": 14, "t": 2026}}
    assert "_strptime" in sensors.inspect_code(MODULE % "import _strptime")[1][0]   # asked for by name: refused


def test_no_domain_no_secrets(monkeypatch):
    monkeypatch.setenv("SENSOR_SECRETS", "SOME_KEY")
    monkeypatch.setenv("SOME_KEY", "v")
    assert sensors.available_secrets(None) == []
    monkeypatch.setattr(sensors.accounts, "trusted", lambda d: True)
    assert sensors.available_secrets("mine") == ["SOME_KEY"]


def test_a_model_read_text_must_be_in_its_quote():
    page = "London weather now\nTemperature 18°C\nConditions: light rain"
    schema = [{"name": "conditions", "type": "string", "required": False}]
    assert sensors.verified({"conditions": "Sunny"}, {"conditions": "Temperature 18°C"}, page, schema) == {}
    assert sensors.verified({"conditions": "Light rain"}, {"conditions": "Conditions: light rain"}, page, schema) == \
        {"conditions": "Light rain"}


def test_a_live_search_and_resting_sources_come_last():
    resting = {"name": "resting", "host": "b.com", "cooldown_until": "2999-01-01T00:00:00+00:00", "last_error": "403"}
    search = {"name": "search", "kind": "search", "host": "live search"}
    same, other = {"name": "same", "host": "a.com"}, {"name": "other", "host": "c.com"}
    assert [m["name"] for m in sensors._route([search, resting, same, other], {})] == \
        ["same", "other", "search", "resting"]
    # After a.com answered: another site first, but never a resting source or the search before a ready source.
    assert [m["name"] for m in sensors._second_opinion([search, resting, same, other], {"a.com"})] == \
        ["other", "same", "search", "resting"]


async def test_vote_confirms_with_an_agreeing_source_and_does_not_rerun_the_outvoted(monkeypatch):
    members = [{"uid": u, "name": u, "host": f"{u}.com", "params": {"symbol": {"example": "NVDA"}},
                "schema": [{"name": "price", "type": "number", "required": True}], "key_fields": ["price"],
                "history": "11", "description": "d"} for u in ("a", "b", "c")]
    prices = {"a": 100.0, "b": 110.0, "c": 100.1}          # b read another number
    asked, written = [], []

    async def fake_members(db, domain, name, **_):
        return members

    async def fake_ask(domain, member, args, schema=None):
        asked.append(member["uid"])
        return {"ok": True, "result": {"price": prices[member["uid"]]}}, 0.5

    async def fake_update(db, domain, type_, uid, changes, *_, **__):
        written.append((uid, changes))
        return True

    monkeypatch.setattr(sensors, "members", fake_members)
    monkeypatch.setattr(sensors, "_ask", fake_ask)
    monkeypatch.setattr(sensors.kgdb, "update_doc", fake_update)
    reading = await sensors.read(None, "d", "quote", verify_field="price")
    agreement = reading["agreement"]
    assert reading["source"] == "a.com" and agreement["other_source"] == "c.com" and agreement["agree"]
    assert agreement["outvoted"] == ["b.com"] and agreement["sources_read"] == 3
    assert sorted(asked) == ["a", "b", "c"]                # each asked once: the outvoted one isn't run again
    last = {uid: c for uid, c in written}
    assert last["b"]["history"] == "110" and last["b"]["last_ok"] is False and "disagreed" in last["b"]["last_error"]
    assert last["a"]["history"] == "111" and last["c"]["history"] == "111"
