"""Entity name keys — the deterministic first step of entity resolution.

Embeddings cannot tell "same entity" from "related entity" (measured with
nomic-embed-text-v2-moe: "CoWoS"~"CoWoS-L" 0.74 and "HBM3E"~"HBM3" 0.81 are
different things, while "SK Hynix"~"SK hynix Inc." 0.71 is the same one). So two
names are merged automatically only when their keys are equal; embeddings just
find candidates for the LLM resolver.
"""

import re
import unicodedata

# Legal-form suffixes that never distinguish one entity from another.
_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "plc", "gmbh", "ag", "sa", "nv", "holdings",
}


def name_key(name: str) -> str:
    """'SK hynix Inc.' -> 'sk hynix', 'NVIDIA Corporation' -> 'nvidia'. Keeps '-', '+', '.' inside
    names, so variants stay distinct ('CoWoS-L' != 'CoWoS')."""
    s = unicodedata.normalize("NFKC", name).lower().replace("&", " and ")
    s = re.sub(r"['’]s\b", "", s)
    s = re.sub(r"[^\w\s\-+.]", " ", s)
    words = s.split()
    if words and words[0] == "the":
        words = words[1:]
    while words and words[-1].rstrip(".") in _SUFFIXES:
        words.pop()
    return " ".join(words).rstrip(".")
