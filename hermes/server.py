"""Hermes service — HTTP entry point for talking to the Head Agent.

    uv run python server.py   → http://localhost:8090

The frontend's Ask panel calls POST /domains/{domain}/ask here. The KG API
(port 8080) must be running: the Head Agent reaches the KG through its MCP server.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from head_agent import DomainNotFound, ask_head, connect_kg


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best effort: if the KG is not up yet, each /ask retries the connection.
    try:
        await asyncio.to_thread(connect_kg)
    except Exception as e:
        print(f"[Hermes] KG MCP not reachable at startup: {type(e).__name__}: {e}")
    yield


app = FastAPI(title="Sovereign Hermes", description="Head Agent over the Sovereign KG", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[Turn] = []
    max_iterations: int = Field(default=8, ge=1, le=30)


@app.post("/domains/{domain_name}/ask")
async def ask(domain_name: str, request: AskRequest):
    """One turn with the Head Agent, grounded in the domain's KG.

    Hermes's orchestration loop is synchronous, so it runs in a worker thread.
    """
    history = [t.model_dump() for t in request.history]
    try:
        answer = await asyncio.to_thread(
            ask_head, domain_name, request.message, history, max_iterations=request.max_iterations
        )
    except DomainNotFound:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Head Agent failed: {type(e).__name__}: {e}")
    return {"domain": domain_name, "message": request.message, "answer": answer}


@app.get("/")
async def root():
    return {"status": "Sovereign Hermes is alive"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8090)
