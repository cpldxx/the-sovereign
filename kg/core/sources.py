"""What kind of source a text came from — and so how far a fact it states can be trusted on its own.

    paper      peer-reviewed papers and preprints (research reads them through OpenAlex, arXiv and PubMed; a DOI or a
               journal's site says so for anything else)
    official   governments, regulators, international and standards bodies, universities and research institutes
    reference  encyclopedias
    press      established news organizations
    user       what a person pasted in (📎) — they vouch for it
    web        anything else: company and trade sites, specialist media
    blog       blogs, forums and social media

A source's tier caps the reliability of each fact it states (like a partial read's cap): one blog post alone makes a
fact that barely enters the graph, one paper a strong one. Confirmation by more sources still adds up either way —
that is the weight (core/database.strengthen_fact). The tier is read from the URL (the research service says "paper"
outright), so it is decided the same way for every source and shown with it.
"""

from urllib.parse import urlsplit

CAPS = {"paper": 1.0, "official": 0.95, "reference": 0.85, "press": 0.85, "user": 0.85, "web": 0.75, "blog": 0.6}
TIERS = tuple(CAPS)
LABELS = {"paper": "a paper", "official": "an official source", "reference": "a reference work", "press": "the press",
          "user": "a source you added", "web": "a website", "blog": "a blog or forum"}

_PAPER = {
    "doi.org", "arxiv.org", "openalex.org", "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "europepmc.org",
    "biorxiv.org", "medrxiv.org", "chemrxiv.org", "ssrn.com", "researchsquare.com", "semanticscholar.org",
    "nature.com", "science.org", "sciencedirect.com", "cell.com", "thelancet.com", "nejm.org", "bmj.com",
    "jamanetwork.com", "link.springer.com", "springer.com", "onlinelibrary.wiley.com", "tandfonline.com",
    "journals.sagepub.com", "academic.oup.com", "cambridge.org", "ieeexplore.ieee.org", "dl.acm.org",
    "aclanthology.org", "openreview.net", "proceedings.neurips.cc", "proceedings.mlr.press", "mdpi.com",
    "frontiersin.org", "plos.org", "pnas.org", "iopscience.iop.org", "pubs.acs.org", "pubs.rsc.org",
    "annualreviews.org", "elifesciences.org", "peerj.com", "jstor.org", "hindawi.com", "karger.com",
    "pubs.aip.org", "journals.aps.org", "ahajournals.org", "jci.org", "embopress.org",
}
_OFFICIAL = {
    "europa.eu", "who.int", "un.org", "oecd.org", "worldbank.org", "imf.org", "wto.org", "iso.org", "iec.ch",
    "itu.int", "jedec.org", "ietf.org", "w3.org", "ieee.org", "nist.gov", "cern.ch", "esa.int", "bis.org",
    "canada.ca", "gc.ca", "gov.uk", "nhs.uk", "service.gov.uk",
}
_OFFICIAL_SUFFIXES = (".gov", ".mil", ".int", ".edu")
_OFFICIAL_LABELS = ("gov", "go", "gob", "gouv", "govt", "mil", "ac", "edu")   # gov.au, go.kr, gouv.fr, ac.uk, ac.kr…
_REFERENCE = {"wikipedia.org", "britannica.com", "namu.wiki", "scholarpedia.org", "plato.stanford.edu"}
_PRESS = {
    "reuters.com", "apnews.com", "afp.com", "bloomberg.com", "ft.com", "wsj.com", "nytimes.com", "washingtonpost.com",
    "economist.com", "bbc.com", "bbc.co.uk", "theguardian.com", "cnbc.com", "npr.org", "aljazeera.com", "axios.com",
    "politico.com", "nikkei.com", "asia.nikkei.com", "scmp.com", "lemonde.fr", "spiegel.de", "nhk.or.jp",
    "yna.co.kr", "koreaherald.com", "koreatimes.co.kr", "joongang.co.kr", "chosun.com", "hani.co.kr", "donga.com",
    "statnews.com", "sciencenews.org", "newscientist.com", "scientificamerican.com", "technologyreview.com",
    "theverge.com", "arstechnica.com", "wired.com", "techcrunch.com",
}
_BLOG = {
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "tistory.com", "blog.naver.com", "cafe.naver.com",
    "brunch.co.kr", "velog.io", "dev.to", "hashnode.dev", "ghost.io", "reddit.com", "quora.com", "x.com",
    "twitter.com", "facebook.com", "instagram.com", "tiktok.com", "youtube.com", "linkedin.com", "threads.net",
    "stackexchange.com", "stackoverflow.com", "dcinside.com", "fmkorea.com", "clien.net",
    "t.me", "telegram.me", "discord.com", "weibo.com", "bsky.app",   # a Telegram channel set a practice question
}


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _path(url: str) -> str:
    try:
        return urlsplit(url).path.lower()
    except ValueError:
        return ""


def _in(host: str, names: set[str]) -> bool:
    """The host is one of these sites or a subdomain of one."""
    return any(host == n or host.endswith("." + n) for n in names)


def tier_of(source: str, hint: str | None = None) -> str:
    """The tier of a source: `hint` when the caller knows (the research service for papers), else from its URL."""
    if hint in CAPS:
        return hint
    if not source.startswith(("http://", "https://")):
        return "user"
    host = _host(source)
    if not host:
        return "web"
    # PubMed Central's older addresses sit on the NIH's own host. Its host and its path, never the whole address: a
    # page elsewhere carrying "ncbi.nlm.nih.gov/pmc" in its path or query was read as a paper.
    if _in(host, _PAPER) or (host == "ncbi.nlm.nih.gov" and _path(source).startswith("/pmc")):
        return "paper"
    if _in(host, _BLOG):
        return "blog"
    if _in(host, _REFERENCE):
        return "reference"
    labels = host.split(".")
    country_gov = len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _OFFICIAL_LABELS   # go.kr, ac.uk
    if _in(host, _OFFICIAL) or host.endswith(_OFFICIAL_SUFFIXES) or country_gov:
        return "official"
    if _in(host, _PRESS):
        return "press"
    return "web"


def cap(tier: str) -> float:
    return CAPS.get(tier, CAPS["web"])
