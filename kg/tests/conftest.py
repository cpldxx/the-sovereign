"""Shared fixtures. Tests that need something running skip themselves when it isn't:
    db       ArcadeDB                  sandbox  Docker + the sensor sandbox image
    live     the four services         slow     the local model / the internet
`./sovereign test` runs everything but slow; `./sovereign test all` runs everything."""

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

KG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KG))
os.chdir(KG)   # the services' modules read .env relative to kg/

from core import auth  # noqa: E402

SERVICE = auth.TOKEN
URLS = {"kg": "http://localhost:8080", "hermes": "http://localhost:8090", "research": "http://localhost:8070"}


def _up(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=30).status_code == 200
    except httpx.HTTPError:
        return False


def _arcadedb() -> bool:
    from core.database import DB_PASS, DB_URL, DB_USER

    try:
        return httpx.get(f"{DB_URL}/api/v1/ready", auth=(DB_USER, DB_PASS), timeout=30).status_code == 204
    except httpx.HTTPError:
        return False


def _docker() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=90).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pytest_collection_modifyitems(config, items):
    checks = {"db": _arcadedb, "sandbox": _docker, "live": lambda: all(_up(u) for u in URLS.values())}
    state = {}
    for item in items:
        for mark, check in checks.items():
            if mark in item.keywords:
                if mark not in state:
                    state[mark] = check()
                if not state[mark]:
                    item.add_marker(pytest.mark.skip(reason=f"needs {mark} (not available)"))


@pytest.fixture
async def db():
    from core.database import ArcadeDB

    client = ArcadeDB()
    yield client
    await client.close()
