"""OpenHands Docker integration.

Spins up an ephemeral OpenHands container per domain generation request.
The container uses qwen2.5-coder:7b via Ollama to write domain-specific tools.py.
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
CODER_MODEL = "openai/qwen2.5-coder:7b"
TASK_TIMEOUT = 300  # seconds

TEMPLATE_DIR = Path(__file__).parent.parent / "domains" / "_template"


async def generate_domain_tools(domain_name: str, config: dict) -> str:
    """
    Spin up a headless OpenHands container to generate tools.py for a domain.

    Mounts a temp workspace with the tools template + domain config.
    OpenHands writes tools.py to the workspace.
    Returns the generated source code.
    """
    with tempfile.TemporaryDirectory(prefix=f"sovereign_{domain_name}_") as workspace:
        workspace_path = Path(workspace)
        _prepare_workspace(workspace_path, domain_name, config)

        task = _build_task(domain_name, config)
        generated_code = await _run_openhands(workspace_path, task)
        return generated_code


def _prepare_workspace(workspace_path: Path, domain_name: str, config: dict) -> None:
    """Copy template files and domain config into the temp workspace."""
    shutil.copy(TEMPLATE_DIR / "tools.py", workspace_path / "tools_template.py")
    (workspace_path / "domain_config.json").write_text(json.dumps(config, indent=2))
    (workspace_path / "TASK.md").write_text(
        f"# Domain: {domain_name}\n\n"
        f"Description: {config.get('description', '')}\n"
        f"Data sources: {', '.join(config.get('data_sources', []))}\n"
        f"Keywords: {', '.join(config.get('keywords', []))}\n"
    )


def _build_task(domain_name: str, config: dict) -> str:
    """Build the natural-language task description passed to OpenHands."""
    data_sources = ", ".join(config.get("data_sources", [])) or "general web sources"
    keywords = ", ".join(config.get("keywords", [])) or "general knowledge"
    description = config.get("description", "")

    return (
        f"Read /opt/workspace_base/tools_template.py. "
        f"Create /opt/workspace_base/tools.py that implements domain-specific tools "
        f"for the '{domain_name}' domain ({description}). "
        f"Implement three async functions that accept (ctx: RunContext, ...) and return str: "
        f"1) collect(ctx, query) - fetches real data from: {data_sources}. "
        f"2) analyze(ctx, data) - applies {domain_name}-specific analysis using keywords: {keywords}. "
        f"3) summarize(ctx, data) - extracts structured knowledge ready to store in a graph. "
        f"Rules: use only Python stdlib + httpx for HTTP. No async generators. "
        f"Keep the 'from pydantic_ai import RunContext' import. "
        f"Write the final file to /opt/workspace_base/tools.py and nothing else."
    )


async def _run_openhands(workspace_path: Path, task: str) -> str:
    """Run the OpenHands container and return the generated tools.py content."""
    loop = asyncio.get_event_loop()

    def _blocking_run() -> str:
        client = docker.from_env()

        container = client.containers.run(
            image=OPENHANDS_IMAGE,
            command=["python", "-m", "openhands.core.main", "-t", task, "-d", "/opt/workspace_base"],
            environment={
                "SANDBOX_RUNTIME_CONTAINER_IMAGE": RUNTIME_IMAGE,
                "SANDBOX_USER_ID": str(os.getuid()),
                "LLM_API_KEY": "ollama",
                "LLM_BASE_URL": "http://host.docker.internal:11434/v1",
                "LLM_MODEL": CODER_MODEL,
                "LOG_ALL_EVENTS": "true",
            },
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                str(workspace_path): {"bind": "/opt/workspace_base", "mode": "rw"},
            },
            detach=True,
            remove=False,
        )

        try:
            result = container.wait(timeout=TASK_TIMEOUT)
            exit_code = result.get("StatusCode", -1)

            output_file = workspace_path / "tools.py"
            if output_file.exists():
                return output_file.read_text()

            # Dump container logs to help debug if output is missing
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            raise RuntimeError(
                f"OpenHands exited with code {exit_code} but tools.py was not written.\n"
                f"Container logs (last 2000 chars):\n{logs[-2000:]}"
            )
        finally:
            container.remove(force=True)

    return await loop.run_in_executor(None, _blocking_run)
