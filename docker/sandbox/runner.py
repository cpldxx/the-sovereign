"""Runs one sensor: reads {"code", "params", "secrets", "pages"} as JSON on stdin, executes the module's run(**params)
and writes {"ok": true, "result": ...} or {"ok": false, "error": ...} as one JSON line on stdout.

Secrets (API keys the module declared in SECRETS) arrive on stdin, never in the environment; the module reads them
with secret("NAME"), and their values are masked in everything written back.
Pages (the module's PAGES, opened in a browser by the KG before this container starts — robots.txt respected) arrive
on stdin too; the module reads one with page("name"). Requests the module makes itself are checked against robots.txt
here."""

import json
import signal
import sys
import traceback
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

LIMIT = 25  # seconds for run() itself; the container has its own hard timeout
BOT = "SovereignBot"


# robots.txt, enforced here for every request a module makes (each redirect hop too): whatever the code says, a
# site that asks automated readers to stay out isn't read. Unreachable robots.txt = no rules; 401/403 = keep out.
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}
_send = httpx.Client._send_single_request


def _allowed(url: httpx.URL) -> bool:
    site = f"{url.scheme}://{url.netloc.decode()}"
    if site not in _robots:
        parser = urllib.robotparser.RobotFileParser()
        try:
            with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": BOT}) as client:
                r = client.get(f"{site}/robots.txt")
            if r.status_code in (401, 403):
                parser.disallow_all = True
            elif r.status_code == 200:
                parser.parse(r.text.splitlines())
            else:
                parser.allow_all = True
        except httpx.HTTPError:
            parser.allow_all = True
        _robots[site] = parser
    return _robots[site].can_fetch(BOT, str(url))


def _send_politely(self, request: httpx.Request) -> httpx.Response:
    if request.url.path != "/robots.txt" and not _allowed(request.url):
        raise PermissionError(f"robots.txt of {urlsplit(str(request.url)).netloc} disallows {request.url}")
    return _send(self, request)


_send_async = httpx.AsyncClient._send_single_request


async def _send_politely_async(self, request: httpx.Request) -> httpx.Response:
    if request.url.path != "/robots.txt" and not _allowed(request.url):
        raise PermissionError(f"robots.txt of {urlsplit(str(request.url)).netloc} disallows {request.url}")
    return await _send_async(self, request)


httpx.Client._send_single_request = _send_politely
httpx.AsyncClient._send_single_request = _send_politely_async


def _timeout(*_):
    raise TimeoutError(f"run() took longer than {LIMIT} s")


def main() -> None:
    job = json.loads(sys.stdin.read())
    secrets: dict = job.get("secrets") or {}
    pages: dict = job.get("pages") or {}

    def secret(name: str) -> str:
        if name not in secrets:
            raise KeyError(f"secret {name!r} is not available (declare it in SECRETS and set it in kg/.env)")
        return secrets[name]

    def page(name: str) -> str:
        if name not in pages:
            raise KeyError(f"page {name!r} is not declared in PAGES")
        if pages[name].get("error"):
            raise RuntimeError(f"page {name!r} could not be opened: {pages[name]['error']}")
        return pages[name]["text"]

    namespace: dict = {"__name__": "sensor", "secret": secret, "page": page}
    try:
        exec(compile(job["code"], "sensor.py", "exec"), namespace)
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(LIMIT)
        result = namespace["run"](**job.get("params", {}))
        signal.alarm(0)
        out = {"ok": True, "result": result}
        json.dumps(out)  # must be JSON-serializable
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        out = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb[-1500:]}
    text = json.dumps(out, default=str)[:200_000]
    for value in secrets.values():
        if value:
            text = text.replace(value, "***")
    sys.stdout.write(text + "\n")


main()
