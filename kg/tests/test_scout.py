"""The Scout's own logic: URL templates from real URLs, the reference filter, the vote between sources, the output
check — and the catalog that learns (with fake embeddings, no services)."""

import pytest

from core import catalog, scout


def test_template_from_a_real_url():
    assert scout._template("https://finance.yahoo.com/quote/NVDA/", {"symbol": "NVDA"}) == \
        "https://finance.yahoo.com/quote/{symbol}/"
    assert scout._template("https://www.morningstar.com/stocks/xnas/nvda/quote", {"symbol": "NVDA"}) == \
        "https://www.morningstar.com/stocks/xnas/{symbol|lower}/quote"
    assert scout._template("https://news.example.com/nvdaily", {"symbol": "NVDA"}) is None   # not a whole token


def test_reference_filter():
    assert scout._shows("Last 236.85 -2.39", 236.8)
    assert not scout._shows("404 Not Found 2026", 236.8)
    assert scout._shows("anything", None)


def _works(price, **rest):
    return {"outcome": "works", "value": {"price": price}, "result": {"price": price, **rest}}


def test_vote_drops_a_previous_close():
    works = [_works(p) for p in (236.9, 237.0, 239.24, 236.95)]
    scout._agreement(works, [], ["price"])
    assert [w["outcome"] == "works" for w in works] == [True, True, False, True]


def test_vote_tolerates_after_hours_but_not_a_flipped_sign():
    works = [_works(236.9, change_percent=-0.74), _works(237.0, change_percent=-0.52),
             _works(237.05, change_percent=0.74), _works(236.95, change_percent=-0.30)]
    scout._agreement(works, [], ["price"], ["change_percent"])
    assert [w["outcome"] == "works" for w in works] == [True, True, False, True]


def test_vote_skips_values_spread_out_by_nature():
    works = [_works(p) for p in (10, 25, 60, 120)]       # e.g. a count of results: nothing to vote on
    scout._agreement(works, [], ["price"])
    assert all(w["outcome"] == "works" for w in works)


def test_output_check():
    fields = [{"name": "price", "numeric": True, "key": True}, {"name": "items", "numeric": False, "key": False,
                                                                 "required": True}]
    assert scout._check({"price": 1.0, "items": [1]}, fields) == ""
    assert "price" in scout._check({"price": "1.0", "items": [1]}, fields)
    assert "items" in scout._check({"price": 1.0, "items": []}, fields)


@pytest.fixture
def fresh_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "PATH", tmp_path / "catalog.json")
    words = ["stock", "price", "share", "quote", "exchange", "currency", "rate", "weather"]

    async def embed(texts):   # bag of words: similar needs → similar vectors
        return [[float(w in t.lower()) for w in words] for t in texts]

    monkeypatch.setattr(catalog.embeddings, "embed", embed)
    return catalog


async def test_catalog_learns_reuses_and_forgets(fresh_catalog):
    c = fresh_catalog
    good = {"host": "a.com", "kind": "page", "url": "https://a.com/q/{symbol}", "outcome": "works"}
    blocked = {"host": "b.com", "kind": "page", "url": "https://b.com/{symbol}", "outcome": "bot protection"}
    learned = await c.record("latest stock price for a ticker", ["symbol"], [good, blocked])
    assert learned["added"] == 1 and learned["refusals"] == 1

    # A similar need, worded differently, with another parameter name: the template comes back, renamed.
    found = await c.suggest("current stock price quote for a company", ["ticker"], set())
    assert [f["url"] for f in found] == ["https://a.com/q/{ticker}"]
    assert await c.suggest("weather in a city", ["city"], set()) == []        # unrelated need
    assert c.refused({"host": "b.com", "url": "https://b.com/{symbol}"})      # remembered refusal

    # Three failures in a row: forgotten.
    for _ in range(3):
        await c.record("latest stock price for a ticker", ["symbol"], [{**good, "outcome": "parser failed: x"}])
    assert await c.suggest("latest stock price for a ticker", ["symbol"], set()) == []
