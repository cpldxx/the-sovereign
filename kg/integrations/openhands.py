"""OpenHands Docker integration.

Spins up an ephemeral OpenHands container per domain generation request.
The container uses CODER_MODEL via Ollama to write domain-specific tools.py.
Container is destroyed after the task completes.
"""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import docker

OPENHANDS_IMAGE = "ghcr.io/all-hands-ai/openhands:0.40"
RUNTIME_IMAGE = "ghcr.io/all-hands-ai/runtime:0.40.0-nikolaik"
# LiteLLM model string; the container reaches Ollama via host.docker.internal.
CODER_MODEL = os.getenv("CODER_MODEL", "openai/qwen2.5-coder:32b")
TASK_TIMEOUT = 900  # seconds (OpenHands needs time to spin up its sandbox + run LLM)

TEMPLATE_DIR = Path(__file__).parent.parent / "domains" / "_template"


async def generate_domain_tools(domain_name: str, config: dict) -> str:
    """
    Spin up a headless OpenHands container to generate tools.py for a domain.

    Mounts a temp workspace with the tools template + domain config.
    OpenHands writes tools.py to the workspace.
    Returns the generated source code.
    """
    with tempfile.TemporaryDirectory(prefix=f"sovereign_{domain_name}_") as root_tmp:
        root_path = Path(root_tmp)
        workspace_path = root_path / "workspace"
        state_path = root_path / "state"
        workspace_path.mkdir()
        state_path.mkdir()
        # OpenHands needs world-writable state dir (runs as non-root inside container)
        state_path.chmod(0o777)
        workspace_path.chmod(0o777)

        _prepare_workspace(workspace_path, domain_name, config)

        task = _build_task(domain_name, config)
        generated_code = await _run_openhands(workspace_path, state_path, task)
        return generated_code


def _prepare_workspace(workspace_path: Path, domain_name: str, config: dict) -> None:
    """Write a skeleton tools.py into the workspace so the agent only fills bodies."""
    data_sources = ", ".join(config.get("data_sources", [])) or "general web sources"
    keywords = ", ".join(config.get("keywords", [])) or "general knowledge"
    description = config.get("description", "")

    skeleton = (
        f'"""Domain tools for {domain_name}: {description}"""\n'
        f"import json\n"
        f"import httpx\n"
        f"from pydantic_ai import RunContext\n\n\n"
        f"async def collect(ctx: RunContext, query: str) -> str:\n"
        f"    \"\"\"Fetch data from {data_sources} for: {{query}}\"\"\"\n"
        f"    # TODO: implement real data fetching from {data_sources}\n"
        f"    return json.dumps({{'query': query, 'data': 'fetched'}})\n\n\n"
        f"async def analyze(ctx: RunContext, data: str) -> str:\n"
        f"    \"\"\"Apply {domain_name} analysis using keywords: {keywords}\"\"\"\n"
        f"    # TODO: implement {domain_name}-specific analysis\n"
        f"    return json.dumps({{'analyzed': data, 'keywords': {keywords!r}}})\n\n\n"
        f"async def summarize(ctx: RunContext, data: str) -> str:\n"
        f"    \"\"\"Extract structured knowledge for the graph\"\"\"\n"
        f"    # TODO: implement knowledge extraction and summarization\n"
        f"    return json.dumps({{'summary': data}})\n"
    )
    (workspace_path / "tools.py").write_text(skeleton)
    # Make workspace world-writable so the sandbox (running as a different uid) can write back
    for f in workspace_path.iterdir():
        f.chmod(0o666)
    (workspace_path / "domain_config.json").write_text(json.dumps(config, indent=2))


def _build_task(domain_name: str, config: dict) -> str:
    """Build the task description passed to OpenHands."""
    data_sources = ", ".join(config.get("data_sources", [])) or "general web sources"
    keywords = ", ".join(config.get("keywords", [])) or "general knowledge"
    description = config.get("description", "")

    return (
        f"Write a Python file /workspace/tools.py for the '{domain_name}' domain "
        f"({description}). "
        f"The file must contain exactly these three async functions:\n"
        f"1. async def collect(ctx, query: str) -> str  "
        f"- fetches live data from {data_sources} using httpx. "
        f"Build a real HTTP request (e.g. GET a public API endpoint) and return the response as a JSON string.\n"
        f"2. async def analyze(ctx, data: str) -> str  "
        f"- parses the JSON from collect, finds values relevant to: {keywords}, returns analysis as JSON string.\n"
        f"3. async def summarize(ctx, data: str) -> str  "
        f"- condenses analyze output into a concise JSON summary with 'title', 'key_facts' list, 'domain' fields.\n"
        f"Required imports at the top: import json, import httpx, from pydantic_ai import RunContext\n"
        f"Rules: no async generators, every function returns str, no external deps beyond httpx.\n"
        f"Run the file with 'python /workspace/tools.py' to verify it has no syntax errors. "
        f"Then write the final version to /workspace/tools.py."
    )


def _cleanup_runtime_containers(client: docker.DockerClient) -> None:
    """Remove any orphaned OpenHands runtime containers left from this or prior runs."""
    try:
        for c in client.containers.list(all=True, filters={"name": "openhands-runtime"}):
            c.remove(force=True)
    except Exception:
        pass  # Best-effort cleanup; don't fail the caller


async def _run_openhands(workspace_path: Path, state_path: Path, task: str) -> str:
    """Run the OpenHands container and return the generated tools.py content."""
    loop = asyncio.get_running_loop()

    def _blocking_run() -> str:
        client = docker.from_env()

        container = client.containers.run(
            image=OPENHANDS_IMAGE,
            command=["python", "-m", "openhands.core.main", "-t", task, "-d", "/workspace"],
            environment={
                "SANDBOX_RUNTIME_CONTAINER_IMAGE": RUNTIME_IMAGE,
                "SANDBOX_USER_ID": str(os.getuid()),
                "LLM_API_KEY": "ollama",
                "LLM_BASE_URL": "http://host.docker.internal:11434/v1",
                "LLM_MODEL": CODER_MODEL,
                "LOG_ALL_EVENTS": "true",
                # Tell OpenHands the HOST path so its sandbox mount lands on disk correctly.
                # Without this, OpenHands passes its internal /opt/workspace_base path to
                # Docker when mounting the sandbox — which resolves to nothing on the host.
                "WORKSPACE_MOUNT_PATH": str(workspace_path),
            },
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                str(workspace_path): {"bind": "/workspace", "mode": "rw"},
                str(state_path): {"bind": "/.openhands-state", "mode": "rw"},
            },
            detach=True,
            remove=False,
        )

        try:
            exit_code = -1
            try:
                result = container.wait(timeout=TASK_TIMEOUT)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout or connection drop — stop cleanly so OpenHands can flush writes.
                container.stop(timeout=15)

            output_file = workspace_path / "tools.py"
            if output_file.exists():
                content = output_file.read_text().strip()
                if content:
                    return content

            # File missing or empty — dump logs for debugging
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OpenHands exited (code {exit_code}) but tools.py was not written.\n"
                f"Container logs (last 3000 chars):\n{logs[-3000:]}"
            )
        finally:
            container.remove(force=True)
            # OpenHands spawns its own runtime container — clean it up too.
            _cleanup_runtime_containers(client)

    return await loop.run_in_executor(None, _blocking_run)
