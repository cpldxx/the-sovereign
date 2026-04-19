"""Ingestor - 데이터 수집 에이전트.

외부 소스에서 데이터를 가져와 SovereignNode 형태로 변환.
"""

from pydantic_ai import Agent

from core.schema import SovereignNode

ingestor = Agent(
    # TODO: Ollama 로컬 모델로 교체
    "openai:gpt-4o-mini",
    system_prompt="""너는 The Sovereign의 수집가(Ingestor)다.

너의 역할:
1. 주어진 원시 데이터를 분석
2. 핵심 지식을 추출
3. SovereignNode 규격에 맞게 정형화

규칙:
- 하나의 입력에서 여러 노드를 추출할 수 있다
- 각 노드는 하나의 명확한 팩트만 담는다
- 출처를 반드시 명시한다
- 신뢰도는 출처의 권위와 검증 가능성을 기반으로 매긴다""",
    output_type=list[SovereignNode],
)


async def ingest_raw_data(raw_text: str, domain: str, source: str) -> list[SovereignNode]:
    """원시 데이터를 SovereignNode 리스트로 변환"""
    prompt = f"""다음 원시 데이터에서 지식 노드를 추출하라:

도메인: {domain}
출처: {source}
데이터:
{raw_text}

각 노드의 uid는 '{domain}:추출한카테고리:일련번호' 형식으로 만들어라."""

    result = await ingestor.run(prompt)
    return result.output
