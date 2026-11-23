"""What kind of source a text came from (core/sources.py) — read the same way for every source — and how it caps the
reliability of the facts it states, as a partial read does. db tests use a throwaway database (conftest.scratch)."""

import pytest

from core import database as kgdb
from core import kg, sources
from core.sources import tier_of


@pytest.mark.parametrize("source,tier", [
    ("https://doi.org/10.1056/NEJMoa2212948", "paper"),
    ("https://arxiv.org/abs/2508.16653", "paper"),
    ("https://pubmed.ncbi.nlm.nih.gov/36449413/", "paper"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/", "paper"),
    ("https://link.springer.com/article/10.1007/s13391-025-00557-9", "paper"),
    ("https://www.ncbi.nlm.nih.gov/books/NBK1/", "official"),
    ("https://someone.wordpress.com/ncbi.nlm.nih.gov/pmc/", "blog"),      # a paper's address inside another site's
    ("https://example.com/read?u=https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/", "web"),
    ("https://www.fda.gov/news-events/leqembi", "official"),
    ("https://www.mss.go.kr/policy", "official"),
    ("https://www.ox.ac.uk/research", "official"),
    ("https://ec.europa.eu/info", "official"),
    ("https://go.com/watch", "web"),                    # "go" is a government label only under a country's TLD
    ("https://en.wikipedia.org/wiki/Lecanemab", "reference"),
    ("https://www.reuters.com/business/healthcare/", "press"),
    ("https://medium.com/@someone/hbm-explained", "blog"),
    ("https://blog.naver.com/someone/123", "blog"),
    ("https://3dfabric.tsmc.com/english/cowos.htm", "web"),
    ("user_input", "user"),
    ("head", "user"),
])
def test_a_source_is_tiered_by_its_url(source, tier):
    assert tier_of(source) == tier


def test_the_caller_may_say_what_it_knows_and_nothing_else():
    assert tier_of("https://example.org/paper.html", "paper") == "paper"   # research read it as a paper
    assert tier_of("https://medium.com/x", "nonsense") == "blog"          # an unknown hint is ignored


def test_a_blog_alone_barely_enters_the_graph_and_a_paper_alone_does():
    assert sources.cap("paper") == 1.0 > sources.cap("official") > sources.cap("press") > sources.cap("web") > \
        sources.cap("blog")
    assert sources.cap("blog") == kg.COMMIT_RELIABILITY                  # a blog's best fact just makes it in
    assert sources.cap("unheard-of") == sources.cap("web")


@pytest.mark.db
async def test_an_episode_keeps_its_tier_and_its_facts_are_capped_by_it(scratch, monkeypatch):
    db, d = scratch
    monkeypatch.setattr(kg, "load_domain", lambda domain: {"ontology": {"entity_types": ["drug"],
                                                                         "relation_types": ["treats"]},
                                                            "config": {"description": "Alzheimer's"}})
    caps = []

    async def read_chunk(domain, description, grammar, text, source, cap, report):
        caps.append(cap)
        return None
    monkeypatch.setattr(kg, "_read_chunk", read_chunk)
    await kg.ingest(db, d, "Someone thinks lecanemab is a miracle. " * 20, source="https://medium.com/@a/b")
    await kg.ingest(db, d, "Lecanemab slowed decline by 27% over 18 months. " * 20,
                    source="https://example.org/nejm-copy", tier="paper")
    await kg.ingest(db, d, "Lecanemab was approved in 2023, the release says. " * 20,
                    source="https://www.fda.gov/news", content_status="partial")
    assert caps == [0.6, 1.0, 0.5]                                        # blog · paper · official read in part
    tiers = {ep["source"]: ep["tier"] for ep in await kgdb.list_episodes(db, d)}
    assert tiers == {"https://medium.com/@a/b": "blog", "https://example.org/nejm-copy": "paper",
                     "https://www.fda.gov/news": "official"}
