"""The Law - 모든 지식 노드의 표준 규격"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SovereignNode(BaseModel):
    """1,000억 개 노드의 표준 규격.
    이 스키마를 통과하지 못하면 DB에 저장 불가 (Blue Apple 방지).
    """

    uid: str = Field(..., description="고유 식별자 (domain:type:hash)")
    domain: Literal["quant", "artisanal", "academic"]
    category: str = Field(..., description="세부 분류 (e.g. 'btc_price', 'perfume_recipe', 'optimization')")
    content: str = Field(..., min_length=1, description="실제 지식 내용")
    source: str = Field(..., description="출처 (URL, 논문명, API 등)")
    tags: list[str] = Field(default_factory=list)
    reliability: float = Field(..., ge=0.0, le=1.0, description="신뢰도 점수 (0~1)")
    created_at: datetime = Field(default_factory=datetime.now)


class SovereignEdge(BaseModel):
    """노드 간 관계 (지식 그래프의 연결선)"""

    from_node: str = Field(..., description="출발 노드 uid")
    to_node: str = Field(..., description="도착 노드 uid")
    relation: str = Field(..., description="관계 유형 (e.g. 'derived_from', 'contradicts', 'supports')")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="관계 강도")
