"""Near-duplicate pages (core/near.py): a copy under other menus or another address is found from the sketches alone,
an excerpt is a copy of its page, a page on the same subject is not; and pages read before sketches get one (kg.read_pages).
Texts are made of numbered words so their overlap is exact; db tests use a throwaway database (conftest.scratch)."""

import pytest

from core import database as kgdb
from core import kg, near


def words(start: int, stop: int) -> str:
    return " ".join(f"word{i}" for i in range(start, stop))


PAGE = words(0, 600)


def test_a_copy_under_other_menus_is_a_copy():
    reprint = f"Home News About Contact\n{PAGE}\nShare Subscribe Privacy"
    assert near.copies(near.sketch(PAGE), near.sketch(reprint))


def test_an_excerpt_is_a_copy_of_its_page_and_a_page_on_the_same_subject_is_not():
    page = near.sketch(PAGE)
    assert near.copies(near.sketch(words(100, 300)), page)          # all of the excerpt is in the page
    assert not near.copies(near.sketch(words(450, 1050)), page)     # a quarter of it in common
    assert not near.copies(near.sketch(words(5000, 5600)), page)


def test_the_estimate_follows_the_overlap():
    a, b = near.sketch(words(0, 1000)), near.sketch(words(500, 1500))
    assert abs(near.jaccard(a["sketch"], b["sketch"]) - 1 / 3) < 0.12    # 500 of 1500 shingles shared
    assert abs(near.held(a, b) - 0.5) < 0.15


def test_a_sketch_is_small_stable_and_exact_as_json():
    s = near.sketch(PAGE)
    assert len(s["sketch"]) == near.K and s["shingles"] == 596 and s == near.sketch(PAGE)
    assert s["sketch"] == sorted(s["sketch"]) and max(s["sketch"]) < 2 ** 52


def test_a_short_text_is_never_called_a_copy():
    phrase = near.sketch(words(0, 20))
    assert not near.copies(phrase, near.sketch(PAGE))
    assert near.sketch("") == {"sketch": [], "shingles": 0}


def test_the_first_page_a_page_copies_is_named():
    read = {"ep1": near.sketch(words(5000, 5600)), "ep2": near.sketch(PAGE), "ep3": {"sketch": None}}
    assert near.copy_in(near.sketch(words(0, 400)), read) == "ep2"
    assert near.copy_in(near.sketch(words(9000, 9400)), read) is None


@pytest.mark.db
async def test_pages_read_before_sketches_get_one_and_unfinished_reads_dont_count(scratch, monkeypatch):
    db, d = scratch
    for uid, status, text in (("ep1", "done", PAGE), ("ep2", "failed", words(2000, 2600)),
                              ("ep3", None, words(3000, 3600)),    # no status: stored before statuses, done
                              ("ep4", "done", "")):
        await kgdb.create_episode(db, d, {"uid": uid, "source": f"https://site.example/{uid}", "content": text,
                                          "content_hash": uid}, status=status)
    pages = await kg.read_pages(db, d)
    assert set(pages) == {"ep1", "ep3", "ep4"} and pages["ep1"] == near.sketch(PAGE)
    stored = {r["uid"]: r for r in await kgdb.episode_sketches(db, d)}
    assert stored["ep1"]["sketch"] == pages["ep1"]["sketch"] and stored["ep1"]["shingles"] == 596
    assert stored["ep2"]["sketch"] is None                          # a failed read is read again: no sketch yet
    assert stored["ep4"]["sketch"] == []                            # an empty page is sketched once, like any other
    computed = []
    monkeypatch.setattr(kg, "_sketch", lambda text: computed.append(text) or near.sketch(text))
    assert await kg.read_pages(db, d) == pages and computed == []    # stored, not computed again
