"""Domain registry - creates and loads domain workspaces."""

import importlib
import json
import re
import shutil
import sys
from pathlib import Path

DOMAINS_DIR = Path(__file__).parent
TEMPLATE_DIR = DOMAINS_DIR / "_template"

DEFAULT_ONTOLOGY = {
    "entity_types": ["concept", "method", "tool", "finding", "event", "metric", "entity"],
    "relation_types": ["supports", "contradicts", "derived_from", "related_to", "precedes", "enables"],
}

# A domain id is a folder name, a Python package name and an ArcadeDB database
# name at once, so it is kept to lowercase identifiers (macOS paths are
# case-insensitive, so "Quant" and "quant" would collide on disk).
_DOMAIN_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")


def domain_id(name: str) -> str:
    """Display name -> domain id ('Quant Trading' -> 'quant_trading')."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not _DOMAIN_ID.fullmatch(slug):
        raise ValueError(
            f"Invalid domain name {name!r}: needs a letter first, then letters, digits or spaces (max 64)"
        )
    return slug


def _domain_dir(name: str) -> Path:
    """Directory of an existing domain. Rejects anything that is not a plain id (no path tricks)."""
    if not _DOMAIN_ID.fullmatch(name) or not (DOMAINS_DIR / name).is_dir():
        raise ValueError(f"Domain '{name}' does not exist")
    return DOMAINS_DIR / name


def create_domain(name: str, description: str, data_sources: list[str] = None, keywords: list[str] = None) -> str:
    """Create a new domain workspace from the template. Returns the domain id."""
    domain = domain_id(name)
    domain_dir = DOMAINS_DIR / domain

    if domain_dir.exists():
        raise ValueError(f"Domain '{domain}' already exists")

    # Copy template
    shutil.copytree(TEMPLATE_DIR, domain_dir)

    # Fill in config. Values are written with repr() so user text can never
    # break out of the string literals into code.
    config = {
        "name": domain,
        "description": description,
        "data_sources": list(data_sources or []),
        "keywords": list(keywords or []),
    }
    body = "".join(f"    {k!r}: {v!r},\n" for k, v in config.items())
    (domain_dir / "config.py").write_text(
        f'"""Domain configuration for {domain}"""\n\nDOMAIN_CONFIG = {{\n{body}}}\n'
    )
    importlib.invalidate_caches()  # let the import system see the new package

    return domain


def ontology_path(name: str) -> Path:
    return _domain_dir(name) / "ontology.json"


def load_domain(name: str) -> dict:
    """Load a domain's config, tools (Coder Agent target) and ontology."""
    domain_dir = _domain_dir(name)

    # Import domain modules dynamically
    config_module = importlib.import_module(f"domains.{name}.config")
    tools_module = importlib.import_module(f"domains.{name}.tools")

    ontology_file = domain_dir / "ontology.json"
    ontology = json.loads(ontology_file.read_text()) if ontology_file.exists() else DEFAULT_ONTOLOGY

    return {
        "config": config_module.DOMAIN_CONFIG,
        "tools": tools_module,
        "ontology": ontology,
    }


def list_domains() -> list[str]:
    """List all created domains."""
    return sorted(
        d.name for d in DOMAINS_DIR.iterdir()
        if d.is_dir() and _DOMAIN_ID.fullmatch(d.name)
    )


def delete_domain(name: str) -> None:
    """Delete a domain workspace."""
    domain_dir = _domain_dir(name)
    shutil.rmtree(domain_dir)
    # Forget the imported modules, or a re-created domain of the same name
    # would silently load the deleted one's config and tools.
    for module in [m for m in sys.modules if m == f"domains.{name}" or m.startswith(f"domains.{name}.")]:
        del sys.modules[module]
