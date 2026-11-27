"""The "does the graph already have this page?" check fails closed: while the KG can't answer, research neither reads
a page (it may be one the exam holds out) nor ingests one on a guess.

Run: ./sovereign test, or cd research && uv run --with pytest python -m pytest -q tests
"""
import asyncio
import json

import httpx
import pytest

from sovereign_research import fetch, jobs, papers

URL = "https://agency.example/approval"
CLIENT = httpx.AsyncClient


def kg(monkeypatch, module, handler):
    """Every KG call of `module` is answered by `handler` (no network)."""
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda **kw: CLIENT(transport=httpx.MockTransport(handler), **kw))


def down(request):
    raise httpx.ConnectError("down")


@pytest.fixture
def run(monkeypatch):
    """A research run on a domain, with no page fetched for real."""
    read = []

    async def fetch_page(url):
        read.append(url)
        return fetch.Page(url, "A page", "text " * 50, "full")
    monkeypatch.setattr(fetch, "fetch_page", fetch_page)
    monkeypatch.setattr(fetch, "current", fetch.Capture(domain="lab"))
    return read


def test_a_page_is_not_read_while_the_kg_cannot_say(run, monkeypatch):
    kg(monkeypatch, fetch, down)
    answer = asyncio.run(fetch.web_fetch_tool.ainvoke({"url": URL}))
    assert answer == fetch.UNCHECKED.format(url=URL)
    assert run == []                                           # not opened
    assert [(p.url, p.status, p.error) for p in fetch.current.pages] == [(URL, "failed", fetch.UNCHECKED_ERROR)]
    # A listing still shows it (reading is the gate), and the URL is asked about again next time.
    assert asyncio.run(fetch.known([URL])) == set() and not fetch.current.checked


def test_a_server_error_or_a_broken_answer_is_no_answer(run, monkeypatch):
    for reply in (httpx.Response(503), httpx.Response(200, text="<html>"), httpx.Response(200, json={}),
                  httpx.Response(200, json=[URL])):
        kg(monkeypatch, fetch, lambda request, reply=reply: reply)
        with pytest.raises(fetch.Unchecked):
            asyncio.run(fetch.known([URL], strict=True))


def test_a_page_is_read_once_the_kg_says_it_is_new(run, monkeypatch):
    kg(monkeypatch, fetch, lambda request: httpx.Response(200, json={"known": []}))
    assert asyncio.run(fetch.web_fetch_tool.ainvoke({"url": URL})).startswith("text")
    assert run == [URL]
    kg(monkeypatch, fetch, lambda request: httpx.Response(200, json={"known": [URL + "/held"]}))
    assert "already has" in asyncio.run(fetch.web_fetch_tool.ainvoke({"url": URL + "/held"}))
    assert run == [URL]


def test_a_paper_is_not_read_while_the_kg_cannot_say(run, monkeypatch):
    kg(monkeypatch, fetch, down)
    opened = []

    async def read(key, full_text):
        opened.append(key)
    monkeypatch.setattr(papers, "read", read)
    monkeypatch.setitem(papers._found, "W1", type("Paper", (), {"url": URL})())
    assert asyncio.run(papers.read_paper_tool.ainvoke({"key": "W1"})) == fetch.UNCHECKED.format(url=URL)
    assert opened == []


def test_unchecked_pages_are_kept_not_ingested(tmp_path, monkeypatch):
    """The KG answers for the ingest but not for /sources: nothing is ingested on a guess; the pages stay on disk for
    the worker's retry, which ingests them once the KG answers."""
    monkeypatch.setattr(jobs, "DATA", tmp_path)
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "PAGES_DIR", tmp_path / "pages")
    calls, answers = [], {"sources": httpx.Response(503)}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/sources"):
            return answers["sources"]
        return httpx.Response(200, json={"facts_created": 2})
    kg(monkeypatch, jobs, handler)

    async def scenario():
        queue = jobs.Jobs()
        kept = {u: {"title": "A page", "status": "full", "markdown": "text"} for u in (URL, URL + "/2")}
        job = {"id": "j1", "domain": "lab", "steps": [], "status": "researching",
               "pages": [{"url": u, "title": "A page", "status": "full", "error": "", "chars": 4} for u in kept]}
        queue.jobs["j1"] = job
        queue._keep(job, kept)
        await queue._ingest(job, kept)
        assert calls == ["/domains/lab/sources"]                                   # no ingest call at all
        assert set(queue._kept(job)) == {URL, URL + "/2"} and job["summary"]["pages_not_ingested"] == 2
        assert all("could not say" in p["ingest"]["error"] for p in job["pages"])

        answers["sources"] = httpx.Response(200, json={"known": [URL]})          # the retry: the KG is back
        await queue._ingest(job, queue._kept(job))
        assert calls[1:] == ["/domains/lab/sources", "/domains/lab/ingest"]
        assert queue._kept(job) == {} and job["summary"]["pages_not_ingested"] == 0
        by_url = {p["url"]: p["ingest"] for p in job["pages"]}
        assert by_url[URL] == {"skipped": "already in the graph"} and by_url[URL + "/2"]["facts_created"] == 2
    asyncio.run(scenario())
    assert json.loads((tmp_path / "jobs.json").read_text())[0]["id"] == "j1"


def test_a_run_that_read_nothing_for_it_says_so_and_is_tried_again(tmp_path, monkeypatch):
    """Every page left unread because the KG was silent: the job names the KG, not the search engines or the network."""
    monkeypatch.setattr(jobs, "DATA", tmp_path)
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "jobs.json")
    monkeypatch.setattr(jobs, "PAGES_DIR", tmp_path / "pages")
    kg(monkeypatch, jobs, lambda request: httpx.Response(200, json={"brief": "a topic", "episodes": []}))
    unread = fetch.Page(URL, "", "", "failed", fetch.UNCHECKED_ERROR)
    monkeypatch.setattr(jobs.runner, "run", lambda *a: ("", [unread]))

    async def scenario():
        queue = jobs.Jobs()
        job = {"id": "j1", "domain": "lab", "mode": "update", "pages": [], "steps": [], "status": "queued"}
        with pytest.raises(jobs.NothingRead) as e:
            await queue._run(job)
        assert e.value.kg_silent and not e.value.offline
        assert job["pages"][0]["error"] == fetch.UNCHECKED_ERROR
    asyncio.run(scenario())
