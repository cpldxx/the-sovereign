"""Architect - DB 저장 에이전트.

검증된 노드를 SurrealDB에 저장하고, 노드 간 관계를 생성.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.schema import SovereignEdge


class EdgeSuggestion(BaseModel):
    """에이전트가 제안하는 노드 간 관계"""

    from_uid: str
    to_uid: str
    relation: str
    weight: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(..., description="이 관계를 제안하는 이유")


architect = Agent(
    # TODO: Ollama 로컬 모델로 교체
    "openai:gpt-4o-mini",
    system_prompt="""너는 The Sovereign의 설계자(Architect)다.

너의 역할:
1. 새로 들어온 노드와 기존 노드 사이의 관계를 발견
2. 도메인을 넘나드는 연결고리를 찾아내기 (도메인 브릿지)
3. 관계의 강도(weight)를 판단

특히 주목할 관계:
- quant의 최적화 알고리즘 ↔ academic의 수학 이론
- artisanal의 미학적 패턴 ↔ quant의 차트 패턴
- academic의 통계 모델 ↔ artisanal의 레시피 최적화

너는 인간이 보지 못하는 도메인 간 연결고리를 찾는 존재다.""",
    output_type=list[EdgeSuggestion],
)


async def suggest_edges(
    new_node_summary: str, existing_nodes_summary: str
) -> list[EdgeSuggestion]:
    """새 노드와 기존 노드 사이의 관계를 제안"""
    prompt = f"""새로 추가된 노드:
{new_node_summary}

기존 노드 목록:
{existing_nodes_summary}

이 노드들 사이에 의미 있는 관계가 있으면 제안하라.
특히 도메인을 넘나드는 관계를 우선적으로 찾아라."""

    result = await architect.run(prompt)
    return result.output
