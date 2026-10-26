"""The platform around the KG: copies that must stay equal, the edge proxy, the launcher, sign-in throttling
(no services needed)."""

import re
from pathlib import Path

from core import accounts, sensors

ROOT = Path(__file__).resolve().parents[2]


def test_auth_copies_are_identical():
    """CLAUDE.md: core/auth.py is copied verbatim to hermes/ and research/ — edit one, copy to the others."""
    kg = (ROOT / "kg/core/auth.py").read_bytes()
    for copy in ("hermes/auth.py", "research/sovereign_research/auth.py"):
        assert (ROOT / copy).read_bytes() == kg, f"{copy} differs from kg/core/auth.py — copy it over"


def test_nginx_overwrites_forwarded_for():
    """uvicorn trusts the leftmost X-Forwarded-For: appending to a client's own let anyone pick their address."""
    conf = (ROOT / "docker/web/nginx.conf").read_text()
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr;", conf)
    assert "$proxy_add_x_forwarded_for" not in conf


def test_launcher_builds_the_sandbox_tag_the_kg_runs():
    """`./sovereign up` builds the image the KG's default SANDBOX_IMAGE names (a .env may override the setting)."""
    default = re.search(r'getenv\("SANDBOX_IMAGE", "([^"]+)"\)', Path(sensors.__file__).read_text())[1]
    assert re.findall(r"docker build .*-t (sovereign-sandbox:\S+)", (ROOT / "sovereign").read_text()) == [default]


def test_throttle_forgets_old_keys(monkeypatch):
    monkeypatch.setattr(accounts, "_failures", {})
    monkeypatch.setattr(accounts, "MAX_TRACKED", 5)
    for i in range(20):
        accounts._note(f"email:random{i}@example.test")
    assert len(accounts._failures) <= 5 and "email:random19@example.test" in accounts._failures
    monkeypatch.setattr(accounts, "FAILURE_WINDOW", 0)
    assert not accounts._throttled("email:random19@example.test")
    assert "email:random19@example.test" not in accounts._failures              # an empty window leaves no key


def test_signups_throttled_per_address(monkeypatch):
    monkeypatch.setattr(accounts, "_failures", {})
    for _ in range(accounts.MAX_FAILURES["signup"]):
        assert not accounts._throttled("signup:1.2.3.4")
        accounts._note("signup:1.2.3.4")
    assert accounts._throttled("signup:1.2.3.4") and not accounts._throttled("signup:5.6.7.8")
