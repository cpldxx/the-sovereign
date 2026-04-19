"""The Sovereign - 메인 진입점.

Phase 1: FastAPI 서버 + 기본 파이프라인 (Ingest → Validate → Store)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agents.gatekeeper import validate_node
from agents.ingestor import ingest_raw_data
from core.database import get_db, query_nodes, store_node


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 DB 연결 관리"""
    app.state.db = await get_db()
    yield
    await app.state.db.close()


app = FastAPI(
    title="The Sovereign",
    description="독립적 인공 지능 체계 - 1,000억 노드 지식 그래프",
    version="0.1.0",
    lifespan=lifespan,
)


class IngestRequest(BaseModel):
    raw_text: str
    domain: str
    source: str


@app.get("/")
async def root():
    return {"status": "The Sovereign is alive", "version": "0.1.0"}


@app.post("/ingest")
async def ingest(request: IngestRequest):
    """Phase 1 파이프라인: 수집 → 검증 → 저장"""

    # Step 1: Ingestor - 원시 데이터를 노드로 변환
    nodes = await ingest_raw_data(request.raw_text, request.domain, request.source)

    results = []
    for node in nodes:
        # Step 2: Gatekeeper - 각 노드 검증
        validation = await validate_node(node)

        if not validation.is_valid:
            results.append({
                "uid": node.uid,
                "status": "rejected",
                "reason": validation.reason,
            })
            continue

        # 신뢰도 보정 적용
        node.reliability = validation.corrected_reliability

        # Step 3: Architect - DB에 저장
        stored = await store_node(app.state.db, node)
        results.append({
            "uid": node.uid,
            "status": "stored",
            "reliability": node.reliability,
        })

    return {
        "total": len(nodes),
        "stored": sum(1 for r in results if r["status"] == "stored"),
        "rejected": sum(1 for r in results if r["status"] == "rejected"),
        "details": results,
    }


@app.get("/nodes")
async def list_nodes(domain: str | None = None):
    """저장된 노드 조회"""
    nodes = await query_nodes(app.state.db, domain)
    return {"nodes": nodes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
