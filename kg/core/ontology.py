"""Ontology — the grammar of a domain's knowledge graph, enforced in code.

Prompts ask the LLM agents to follow the ontology; this module makes it binding:
a node whose category is outside `entity_types`, or an edge whose relation is
outside `relation_types`, never reaches the database.
"""

import json
import re

from pydantic import BaseModel, field_validator

from domains.registry import ontology_path

_NON_IDENT = re.compile(r"[^a-z0-9]+")


def normalize_type(name: str) -> str:
    """'Price Pattern' -> 'price_pattern' — the form stored in the KG."""
    return _NON_IDENT.sub("_", name.strip().lower()).strip("_")


class Ontology(BaseModel):
    entity_types: list[str]
    relation_types: list[str]

    @field_validator("entity_types", "relation_types")
    @classmethod
    def _normalize(cls, types: list[str]) -> list[str]:
        out = [t for t in dict.fromkeys(map(normalize_type, types)) if t and not t[0].isdigit()]
        if not out:
            raise ValueError("must contain at least one type")
        return out


def save_ontology(domain: str, ontology: Ontology) -> None:
    ontology_path(domain).write_text(json.dumps(ontology.model_dump(), indent=2))
