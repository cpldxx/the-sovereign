"""Runs one sensor: reads {"code", "params"} as JSON on stdin, executes the module's run(**params) and writes
{"ok": true, "result": ...} or {"ok": false, "error": ...} as one JSON line on stdout."""

import json
import signal
import sys
import traceback

LIMIT = 25  # seconds for run() itself; the container has its own hard timeout


def _timeout(*_):
    raise TimeoutError(f"run() took longer than {LIMIT} s")


def main() -> None:
    job = json.loads(sys.stdin.read())
    namespace: dict = {"__name__": "sensor"}
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
    sys.stdout.write(json.dumps(out, default=str)[:200_000] + "\n")


main()
