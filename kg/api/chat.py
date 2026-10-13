"""A person's conversation with the Head (core/chat.py)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import auth, chat

router = APIRouter(tags=["chat"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    status: Literal["pending", "done", "error"] = "done"
    steps: list = Field(default_factory=list)
    user: str | None = Field(default=None, description="Services only (Hermes): whose conversation")


class Change(BaseModel):
    content: str | None = None
    status: Literal["pending", "done", "error"] | None = None
    steps: list | None = None
    user: str | None = None


class Import(BaseModel):
    turns: list[dict] = Field(default_factory=list, max_length=chat.LIMIT)


def _whose(domain: str, user: str | None) -> str:
    """A person's own conversation; a service (Hermes) names the person."""
    principal = auth.require(domain)
    if principal.kind == "user":
        return principal.user["uid"]
    if not user:
        raise HTTPException(status_code=422, detail="user: whose conversation")
    return user


@router.get("/domains/{domain_name}/chat")
async def api_chat(domain_name: str, req: Request, user: str | None = None):
    """Your conversation with the Head, oldest first; an answer still being written has status "pending"."""
    return {"turns": await chat.turns(req.app.state.db, domain_name, _whose(domain_name, user))}


@router.post("/domains/{domain_name}/chat", status_code=201)
async def api_add(domain_name: str, turn: Turn, req: Request):
    who = _whose(domain_name, turn.user)
    return await chat.add(req.app.state.db, domain_name, who, turn.role, turn.content, turn.status, turn.steps)


@router.patch("/domains/{domain_name}/chat/{uid}")
async def api_update(domain_name: str, uid: str, change: Change, req: Request):
    who = _whose(domain_name, change.user)
    if not await chat.update(req.app.state.db, domain_name, who, uid, change.model_dump()):
        raise HTTPException(status_code=404, detail=f"No turn '{uid}' in your conversation")
    return {"status": "updated"}


@router.delete("/domains/{domain_name}/chat")
async def api_clear(domain_name: str, req: Request):
    await chat.clear(req.app.state.db, domain_name, _whose(domain_name, None))
    return {"status": "cleared"}


@router.post("/domains/{domain_name}/chat/import")
async def api_import(domain_name: str, body: Import, req: Request):
    """Move this browser's old local conversation here (only into an empty one)."""
    items = [t for t in body.turns if t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)]
    return {"imported": await chat.import_turns(req.app.state.db, domain_name, _whose(domain_name, None), items)}
