"""Domain registry - creates and loads domain workspaces."""

import importlib
import shutil
from pathlib import Path

DOMAINS_DIR = Path(__file__).parent
TEMPLATE_DIR = DOMAINS_DIR / "_template"


def create_domain(name: str, description: str, data_sources: list[str] = None, keywords: list[str] = None) -> Path:
    """Create a new domain workspace from the template."""
    domain_dir = DOMAINS_DIR / name

    if domain_dir.exists():
        raise ValueError(f"Domain '{name}' already exists")

    # Copy template
    shutil.copytree(TEMPLATE_DIR, domain_dir)

    # Fill in config
    config_path = domain_dir / "config.py"
    config_path.write_text(f'''"""Domain configuration for {name}"""

DOMAIN_CONFIG = {{
    "name": "{name}",
    "description": "{description}",
    "data_sources": {data_sources or []},
    "keywords": {keywords or []},
    "surreal_namespace": "{name}",
}}
''')

    return domain_dir


def load_domain(name: str) -> dict:
    """Load a domain's config, tools, and prompts."""
    domain_dir = DOMAINS_DIR / name

    if not domain_dir.exists():
        raise ValueError(f"Domain '{name}' does not exist")

    # Import domain modules dynamically
    config_module = importlib.import_module(f"domains.{name}.config")
    tools_module = importlib.import_module(f"domains.{name}.tools")
    prompts_module = importlib.import_module(f"domains.{name}.prompts")

    return {
        "config": config_module.DOMAIN_CONFIG,
        "tools": tools_module,
        "prompts": prompts_module,
    }


def list_domains() -> list[str]:
    """List all created domains."""
    return [
        d.name for d in DOMAINS_DIR.iterdir()
        if d.is_dir() and d.name != "_template" and d.name != "__pycache__"
    ]


def delete_domain(name: str) -> None:
    """Delete a domain workspace."""
    domain_dir = DOMAINS_DIR / name

    if not domain_dir.exists():
        raise ValueError(f"Domain '{name}' does not exist")

    if name == "_template":
        raise ValueError("Cannot delete the template")

    shutil.rmtree(domain_dir)
