"""The synthesis (core/synthesis.py): a living review of each core question of the research charter — written from
the graph's cited facts, rewritten when they change."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import auth, charter, synthesis

router = APIRouter(tags=["synthesis"])


class Write(BaseModel):
    wait: bool = False    # write now and return how it went (minutes with a local model); otherwise in the background
    force: bool = False   # every page, changed or not


@router.get("/domains/{domain_name}/synthesis")
async def api_synthesis(domain_name: str, req: Request):
    """The review's pages in the charter's order: each core question's summary, consensus, debates and open
    questions (every point with the facts it cites) and its key sources; and whether it is being written."""
    auth.require(domain_name)
    return {"pages": await synthesis.pages(req.app.state.db, domain_name), "running": synthesis.running(domain_name)}


@router.post("/domains/{domain_name}/synthesis")
async def api_write(domain_name: str, req: Request, body: Write | None = None):
    """Rewrite the pages whose facts changed. Without a charter: {skipped}."""
    auth.require(domain_name, "editor")
    db, body = req.app.state.db, body or Write()
    if not (await charter.get(db, domain_name))["charter"]:
        return {"skipped": "no research charter yet"}
    if body.wait:
        try:
            return await synthesis.refresh(db, domain_name, body.force)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
    if not synthesis.start(db, domain_name, body.force):
        return {"busy": True}
    return JSONResponse({"started": True}, status_code=202)
