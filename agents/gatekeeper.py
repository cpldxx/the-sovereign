"""Gatekeeper - Blue Apple 방지 검증 에이전트.

모든 데이터가 DB에 들어가기 전에 이 에이전트를 통과해야 한다.
Pydantic AI가 LLM을 써서 논리적 정합성까지 검증.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from core.schema import SovereignNode


class ValidationResult(BaseModel):
    """검증 결과 (타입 강제)"""

    is_valid: bool
    reason: str = Field(..., description="검증 통과/실패 이유")
    corrected_reliability: float = Field(
        ..., ge=0.0, le=1.0, description="보정된 신뢰도 점수"
    )


gatekeeper = Agent(
    # TODO: Ollama 로컬 모델로 교체 (e.g. 'ollama:nemotron')
    "openai:gpt-4o-mini",
    system_prompt="""너는 The Sovereign의 검문소(Gatekeeper)다.

너의 역할:
1. 들어오는 데이터가 논리적으로 말이 되는지 검증
2. "파란 사과" 같은 허위 정보를 차단
3. 신뢰도 점수를 보정

검증 기준:
- 출처가 명확한가?
- 내용이 해당 도메인의 상식과 일치하는가?
- 자기모순이 없는가?

엄격하게 판단하라. 의심스러우면 차단이다.""",
    output_type=ValidationResult,
)


async def validate_node(node: SovereignNode) -> ValidationResult:
    """노드가 DB에 들어가기 전 검증"""
    prompt = f"""다음 지식 노드를 검증하라:

도메인: {node.domain}
카테고리: {node.category}
내용: {node.content}
출처: {node.source}
현재 신뢰도: {node.reliability}

이 정보가 논리적으로 타당한지, 허위 정보는 아닌지 판단하라."""

    result = await gatekeeper.run(prompt)
    return result.output
