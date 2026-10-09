"""The Head's team (core/crew.py): who is on it, what each agent is doing, and their conversations with the Head."""

from fastapi import APIRouter, HTTPException, Request

from core import auth, crew

router = APIRouter(tags=["crew"])


@router.get("/domains/{domain_name}/team")
async def api_team(domain_name: str, req: Request):
    """Every agent with its role, tasks and state here (working / queued / idle), and the Head's unread count."""
    auth.require(domain_name)
    db = req.app.state.db
    return {"agents": await crew.team(db, domain_name), "unread": await crew.unread(db, domain_name)}


@router.get("/domains/{domain_name}/threads")
async def api_threads(domain_name: str, req: Request, agent: str | None = None, limit: int = 40):
    """The domain's conversations between the Head and its agents, most recently active first."""
    auth.require(domain_name)
    return {"threads": await crew.threads(req.app.state.db, domain_name, agent, min(limit, 200))}


@router.get("/domains/{domain_name}/threads/{uid}")
async def api_thread(domain_name: str, uid: str, req: Request):
    """One conversation: the thread and every message, oldest first."""
    auth.require(domain_name)
    if not (t := await crew.thread(req.app.state.db, domain_name, uid)):
        raise HTTPException(status_code=404, detail=f"No thread '{uid}'")
    return t
