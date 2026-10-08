"""System routes: the security self-check (the services and admins)."""

from fastapi import APIRouter, HTTPException, Request

from core import auth, selfcheck

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/selfcheck")
async def run_selfcheck(req: Request, alert: bool = True):
    """Check the safeguards on the running system (sandbox egress, robots.txt, webhooks, auth, domain scope); a
    failure becomes a critical alert in every domain. Runs every night before research."""
    principal = auth.current.get()
    if principal.kind != "service" and not (principal.user or {}).get("admin"):
        raise HTTPException(status_code=403, detail="Only for the services or an admin")
    return await selfcheck.run(req.app.state.db, alert=alert)
