"""Near-duplicate pages: the same text under another address (a reprint, a mirror, a syndicated copy, a page and its
excerpt). Comparing addresses misses them; comparing whole texts is too slow for every page the graph has read.

A page's sketch is the K smallest hashes of its five-word shingles: a bottom-k MinHash (Broder 1997; Cohen & Kaplan
2007). Two sketches estimate the Jaccard similarity of the pages' shingle sets, and with the shingle counts, how much
of one page the other holds. Two pages are copies when most of the smaller one is in the larger one.
The estimate rests on the hashes the two pages share among the K smallest of both: a short page inside one more than
about ten times its size shares few of them, and may be missed.
"""

import hashlib
import re

K = 128           # hashes kept per page
SHINGLE = 5       # words per shingle
SAME = 0.5        # share of the smaller page's shingles the larger one holds that makes them copies
MIN_SHINGLES = 50  # below this a page is too short to tell a copy from a shared phrase
_MASK = (1 << 52) - 1   # hashes stay exact as JSON doubles (the database's HTTP API)
_WORD = re.compile(r"\w+")


def sketch(text: str) -> dict:
    """{"sketch": the K smallest shingle hashes, ascending; "shingles": how many distinct shingles the text has}."""
    words = _WORD.findall(text.lower())
    grams = {" ".join(words[i:i + SHINGLE]) for i in range(max(1, len(words) - SHINGLE + 1))} if words else set()
    hashes = sorted({int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big") & _MASK
                     for g in grams})
    return {"sketch": hashes[:K], "shingles": len(hashes)}


def jaccard(a: list[int], b: list[int]) -> float:
    """Estimated Jaccard similarity of two pages' shingle sets, from their sketches."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    union = sorted(sa | sb)[:K]    # the union's K smallest hashes: each is in a page's sketch if it is in the page
    return sum(1 for h in union if h in sa and h in sb) / len(union)


def held(a: dict, b: dict) -> float:
    """Estimated share of page a's shingles that page b holds too: |A∩B| / |A|, with |A∩B| = J·(|A|+|B|)/(1+J)."""
    j = jaccard(a["sketch"], b["sketch"])
    if not j or not a["shingles"]:
        return 0.0
    return min(1.0, j * (a["shingles"] + b["shingles"]) / ((1 + j) * a["shingles"]))


def copies(a: dict, b: dict) -> bool:
    """Whether the two pages are copies of each other: most of the smaller one is in the larger one."""
    small, large = (a, b) if a["shingles"] <= b["shingles"] else (b, a)
    return small["shingles"] >= MIN_SHINGLES and held(small, large) >= SAME


def copy_in(page: dict, others: dict[str, dict]) -> str | None:
    """The key of the first of `others` ({key: sketch}) that `page` is a copy of, else None."""
    return next((key for key, other in others.items() if other.get("sketch") and copies(page, other)), None)
