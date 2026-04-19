"""SurrealDB 연결 및 CRUD 로직"""

from surrealdb import Surreal

from core.schema import SovereignEdge, SovereignNode

DB_URL = "ws://localhost:8000/rpc"
DB_NAMESPACE = "sovereign"
DB_DATABASE = "brain"


async def get_db() -> Surreal:
    db = Surreal(DB_URL)
    await db.connect()
    await db.use(DB_NAMESPACE, DB_DATABASE)
    await db.signin({"user": "root", "pass": "sovereign_pass"})
    return db


async def store_node(db: Surreal, node: SovereignNode) -> dict:
    """검증된 노드를 DB에 저장"""
    result = await db.create("knowledge_node", node.model_dump(mode="json"))
    return result


async def store_edge(db: Surreal, edge: SovereignEdge) -> dict:
    """노드 간 관계를 DB에 저장"""
    result = await db.query(
        "RELATE $from_node->$relation->$to_node SET weight = $weight",
        {
            "from_node": edge.from_node,
            "to_node": edge.to_node,
            "relation": edge.relation,
            "weight": edge.weight,
        },
    )
    return result


async def query_nodes(db: Surreal, domain: str | None = None) -> list[dict]:
    """노드 조회 (도메인 필터 가능)"""
    if domain:
        result = await db.query(
            "SELECT * FROM knowledge_node WHERE domain = $domain",
            {"domain": domain},
        )
    else:
        result = await db.query("SELECT * FROM knowledge_node")
    return result
