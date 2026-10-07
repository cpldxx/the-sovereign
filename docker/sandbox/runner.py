"""Runs one sensor: reads {"code", "params", "secrets", "pages"} as JSON on stdin, executes the module's run(**params)
and writes {"ok": true, "result": ...} or {"ok": false, "error": ...} as one JSON line on stdout.

Secrets (API keys the module declared in SECRETS) arrive on stdin, never in the environment; the module reads them
with secret("NAME"), and their values are masked in everything written back.
Pages (the module's PAGES, opened in a browser by the KG before this container starts — robots.txt respected) arrive
on stdin too; the module reads one with page("name")."""

import json
import signal
import sys
import traceback

LIMIT = 25  # seconds for run() itself; the container has its own hard timeout


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
