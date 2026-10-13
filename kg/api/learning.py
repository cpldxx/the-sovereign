"""The learning cycle's settings and steps (core/learning.py); the research service runs the cycles."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import auth, learning

router = APIRouter(tags=["learning"])


class Forget(BaseModel):
    days: int | None = None      # default: the domain's forget_after_days
    dry_run: bool = True


@router.get("/domains/{domain_name}/learning")
async def api_learning(domain_name: str, req: Request):
    """When this domain learns by itself, what it pursues and forgets, and how the last cycle went."""
    auth.require(domain_name)
    db = req.app.state.db
    s = await learning.settings(db, domain_name)
    # `due`: the slot whose window is open now — the research service starts it unless it already ran it.
    return {"settings": s, "last": await learning.last_cycle(db, domain_name),
            "due": learning.due(s, datetime.now(timezone.utc), set())}


@router.put("/domains/{domain_name}/learning")
async def api_save(domain_name: str, changes: dict, req: Request):
    """Change the schedule (times HH:MM in `timezone`), curiosity (questions a cycle) or forget_after_days."""
    auth.require(domain_name, "editor")
    try:
        return {"settings": await learning.save(req.app.state.db, domain_name, changes)}
    except (learning.LearningError, ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/domains/{domain_name}/learning/gaps")
async def api_gaps(domain_name: str, req: Request):
    """What the graph is unsure or silent about now — the next cycle's starting point."""
    auth.require(domain_name)
    return await learning.gaps(req.app.state.db, domain_name)


@router.post("/domains/{domain_name}/learning/questions")
async def api_questions(domain_name: str, req: Request, n: int | None = None):
    """The Strategist's research questions for the current gaps (n defaults to the domain's curiosity)."""
    auth.require(domain_name, "editor")
    db = req.app.state.db
    count = (await learning.settings(db, domain_name))["curiosity"] if n is None else max(0, min(n, 5))
    return await learning.questions(db, domain_name, count)


@router.post("/domains/{domain_name}/learning/forget")
async def api_forget(domain_name: str, body: Forget, req: Request):
    """Retire unconfirmed weak facts older than `days` (dry_run: only count them)."""
    auth.require(domain_name, "editor")
    db = req.app.state.db
    days = (await learning.settings(db, domain_name))["forget_after_days"] if body.days is None else body.days
    return await learning.forget(db, domain_name, days, body.dry_run)


@router.put("/domains/{domain_name}/learning/last")
async def api_record(domain_name: str, summary: dict, req: Request):
    """The research service records how a cycle went."""
    auth.require(domain_name, "editor")
    await learning.record_cycle(req.app.state.db, domain_name, summary)
    return {"status": "recorded"}
