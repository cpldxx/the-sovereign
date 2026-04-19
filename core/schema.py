"""The Law - Standard schema for all knowledge nodes"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SovereignNode(BaseModel):
    """Standard schema for knowledge nodes.
    Any data that fails this schema cannot enter the DB (Blue Apple prevention).
    """

    uid: str = Field(..., description="Unique identifier (domain:type:hash)")
    domain: Literal["quant", "artisanal", "academic"]
    category: str = Field(..., description="Sub-category (e.g. 'btc_price', 'perfume_recipe', 'optimization')")
    content: str = Field(..., min_length=1, description="Actual knowledge content")
    source: str = Field(..., description="Source (URL, paper name, API, etc.)")
    tags: list[str] = Field(default_factory=list)
    reliability: float = Field(..., ge=0.0, le=1.0, description="Reliability score (0~1)")
    created_at: datetime = Field(default_factory=datetime.now)


class SovereignEdge(BaseModel):
    """Relationship between nodes (edges of the knowledge graph)"""

    from_node: str = Field(..., description="Source node uid")
    to_node: str = Field(..., description="Target node uid")
    relation: str = Field(..., description="Relation type (e.g. 'derived_from', 'contradicts', 'supports')")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="Relation strength")
