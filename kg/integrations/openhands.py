"""Coder backend: OpenHands (headless, in Docker) writes a sensor module.

An ephemeral OpenHands container gets a workspace with the sensor contract and the need; its agent can browse,
run shell commands and test code in its own runtime container, then writes /workspace/sensor.py. The result
goes through the same static check and sandbox test as the built-in Coder's (core/coding.py).

Note: OpenHands needs the Docker socket (it starts its runtime container), and its runtime has full network
access while it develops — unlike the sensor sandbox. The module it writes still only ever runs in the sandbox.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import docker

from core.sensors import contract

OPENHANDS_IMAGE = os.getenv("OPENHANDS_IMAGE", "ghcr.io/all-hands-ai/openhands:0.40")
RUNTIME_IMAGE = os.getenv("OPENHANDS_RUNTIME_IMAGE", "ghcr.io/all-hands-ai/runtime:0.40.0-nikolaik")
# LiteLLM model string; the container reaches Ollama via host.docker.internal.
CODER_MODEL = os.getenv("CODER_MODEL", "openai/qwen3.6:35b")
TASK_TIMEOUT = int(os.getenv("OPENHANDS_TIMEOUT", "1800"))  # seconds


def _task(need: str, domain: str, description: str) -> str:
    return (
        f"Domain: {domain} — {description}\nNEED: {need}\n\n"
        "Write the file /workspace/sensor.py following /workspace/CONTRACT.md exactly. Find a free, keyless, public "
        "data source (a documented API or feed meant for programmatic use) for the need, check what it returns, "
        "write the module, then test it: `pip install httpx` if needed and run "
        "`cd /workspace && python -c \"import sensor, json; print(json.dumps(sensor.run(**{k: v['example'] for k, v "
        "in sensor.PARAMS.items()})))\"`. Fix it until it prints real data that answers the need. Never invent "
        "endpoints or fields; never work around logins, paywalls, CAPTCHAs or rate limits."
    )


async def write_sensor(need: str, domain: str, description: str) -> tuple[str | None, list[str]]:
    """(the module OpenHands wrote, or None; a log tail). Raises at once where it can't work (the caller falls back
    to the built-in Coder)."""
    if Path("/.dockerenv").exists():
        # The workspace would be a temp dir inside this container; the host's Docker daemon can't bind-mount it, and
        # the run used to sit out the whole TASK_TIMEOUT (30 min) for nothing.
        raise RuntimeError("OpenHands can't run from inside the KG container (the host's Docker can't mount its "
                           "workspace) — the built-in Coder writes the sensor")
    return await asyncio.get_running_loop().run_in_executor(None, _run, need, domain, description)


def _runtimes(client) -> set[str]:
    return {c.id for c in client.containers.list(all=True, filters={"name": "openhands-runtime"})}


def _run(need: str, domain: str, description: str) -> tuple[str | None, list[str]]:
    client = docker.from_env()
    before = _runtimes(client)   # runtime containers that aren't this run's (requests run one at a time)
    with tempfile.TemporaryDirectory(prefix=f"sovereign_{domain}_") as root:
        workspace, state = Path(root) / "workspace", Path(root) / "state"
        workspace.mkdir()
        state.mkdir()
        (workspace / "CONTRACT.md").write_text(contract(domain))
        # OpenHands runs as another uid inside its containers.
        for d in (workspace, state):
            d.chmod(0o777)
        (workspace / "CONTRACT.md").chmod(0o666)
        container = client.containers.run(
            image=OPENHANDS_IMAGE,
            command=["python", "-m", "openhands.core.main", "-t", _task(need, domain, description), "-d", "/workspace"],
            environment={
                "SANDBOX_RUNTIME_CONTAINER_IMAGE": RUNTIME_IMAGE,
                "SANDBOX_USER_ID": str(os.getuid()),
                "LLM_API_KEY": "ollama",
                "LLM_BASE_URL": "http://host.docker.internal:11434/v1",
                "LLM_MODEL": CODER_MODEL,
                "LOG_ALL_EVENTS": "true",
                # The HOST path, so the runtime container's workspace mount lands on this directory.
                "WORKSPACE_MOUNT_PATH": str(workspace),
            },
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                str(workspace): {"bind": "/workspace", "mode": "rw"},
                str(state): {"bind": "/.openhands-state", "mode": "rw"},
            },
            detach=True,
        )
        try:
            try:
                container.wait(timeout=TASK_TIMEOUT)
            except Exception:
                container.stop(timeout=15)
            logs = container.logs().decode("utf-8", errors="replace").splitlines()
            module = workspace / "sensor.py"
            code = module.read_text().strip() if module.exists() else None
            return code or None, logs[-40:]
        finally:
            container.remove(force=True)
            # Only the runtime this run started — not every OpenHands runtime on the host (2026-10-14).
            for cid in _runtimes(client) - before:
                try:
                    client.containers.get(cid).remove(force=True)
                except docker.errors.APIError:
                    pass
