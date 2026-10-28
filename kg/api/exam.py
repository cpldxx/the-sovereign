"""The exam (core/exam.py): how much of the research charter the graph can answer — the score over time, each core
question's state, and every question's answer from the graph alone."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core import auth, exam

router = APIRouter(tags=["exam"])


class Sit(BaseModel):
    wait: bool = False   # take it now and return the result (minutes with a local model); otherwise in the background
    fresh: bool = False  # new questions from the same charter first (the score starts a new series)


@router.get("/domains/{domain_name}/exam")
async def api_exam(domain_name: str, req: Request):
    """The exam (its questions, version), the latest sitting with every answer, the history of scores (oldest first),
    and whether a sitting is running."""
    auth.require(domain_name)
    db = req.app.state.db
    return {"exam": await exam.current(db, domain_name), "latest": await exam.latest(db, domain_name),
            "history": await exam.history(db, domain_name), "running": exam.running(domain_name)}


@router.post("/domains/{domain_name}/exam")
async def api_sit(domain_name: str, req: Request, body: Sit | None = None):
    """Take the exam: set (again) from the charter if it changed, every question answered from the graph alone.
    A domain without a charter is skipped — {skipped: why}."""
    auth.require(domain_name, "editor")
    db = req.app.state.db
    if not (await exam.charter.get(db, domain_name))["charter"]:
        return {"skipped": "no research charter yet"}
    body = body or Sit()
    if body.wait:
        try:
            return await exam.take(db, domain_name, body.fresh)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except exam.ExamError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if not exam.start(db, domain_name, body.fresh):
        return {"busy": True}
    return JSONResponse({"started": True}, status_code=202)
