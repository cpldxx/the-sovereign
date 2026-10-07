"""Hermes service — HTTP entry point for talking to the Head Agent.

    uv run python server.py   → http://localhost:8090

The frontend's Ask panel calls POST /domains/{domain}/ask/stream here (SSE). The KG API
(port 8080) must be running: the Head Agent reaches the KG through its MCP server.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import auth
import voice
from head_agent import HEAD_MODEL, TRACING, DomainNotFound, ask_head, warm_up


@asynccontextmanager
async def lifespan(app: FastAPI):
    # (Each domain's KG connection is made on its first question.) Load the speech model in the background, so the first spoken question isn't the slow one.
    warm = asyncio.create_task(asyncio.to_thread(voice.warm_up))
    yield
    warm.cancel()


app = FastAPI(title="Sovereign Hermes", description="Head Agent over the Sovereign KG", lifespan=lifespan)

# Added before CORS, so CORS stays outermost and a 401 still carries CORS headers.
app.add_middleware(auth.Auth)
app.add_middleware(
    CORSMiddleware,
    allow_origins=auth.ORIGINS,
    allow_credentials=True,   # the session cookie
    allow_methods=["*"],
    allow_headers=["*"],
)


def _tools(domain: str) -> str:
    """Editors get the Head's full tool set; viewers the read-only one."""
    return "head" if auth.current.get().can(domain, "editor") else "readonly"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[Turn] = []
    max_iterations: int = Field(default=8, ge=1, le=30)
    voice: bool = Field(default=False, description="Spoken conversation: short answers meant to be read aloud")


@app.post("/domains/{domain_name}/ask")
async def ask(domain_name: str, request: AskRequest):
    """One turn with the Head Agent, grounded in the domain's KG.

    Hermes's orchestration loop is synchronous, so it runs in a worker thread.
    """
    history = [t.model_dump() for t in request.history]
    try:
        answer = await asyncio.to_thread(
            ask_head, domain_name, request.message, history, max_iterations=request.max_iterations,
            voice=request.voice, tools=_tools(domain_name),
        )
    except DomainNotFound:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' does not exist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Head Agent failed: {type(e).__name__}: {e}")
    return {"domain": domain_name, "message": request.message, "answer": answer}


@app.post("/domains/{domain_name}/ask/stream")
async def ask_stream(domain_name: str, request: AskRequest):
    """Same as /ask, streamed as Server-Sent Events while the Head Agent works.

    Events (JSON in `data:`):
      {"type": "tool_start", "id", "name", "args"}   a KG tool call began
      {"type": "tool_end",   "id", "name", "uids", "result"}
                                                     it returned: KG node uids it read/stored,
                                                     result JSON (truncated)
      {"type": "delta",      "text"}                 streamed assistant text
      {"type": "answer",     "text"}                 final answer — last event
      {"type": "error",      "detail"}               failed — last event
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    tools = _tools(domain_name)

    def emit(event: dict | None) -> None:  # called from the agent's worker thread
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run() -> None:
        try:
            answer = ask_head(
                domain_name,
                request.message,
                [t.model_dump() for t in request.history],
                max_iterations=request.max_iterations,
                on_event=emit,
                voice=request.voice,
                tools=tools,
            )
            emit({"type": "answer", "text": answer})
        except DomainNotFound:
            emit({"type": "error", "detail": f"Domain '{domain_name}' does not exist"})
        except Exception as e:
            emit({"type": "error", "detail": f"Head Agent failed: {type(e).__name__}: {e}"})
        finally:
            emit(None)

    worker = asyncio.create_task(asyncio.to_thread(run))

    async def events():
        while (event := await queue.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"
        await worker

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)


_background: set[asyncio.Task] = set()


@app.post("/voice/warm", status_code=202)
async def voice_warm(domain: str | None = None):
    """Load the speech model, the Head's LLM and the domain's embedding model now (when a voice conversation
    starts), so the first spoken question isn't the slow one."""
    if domain:
        auth.require(domain)
    for job in (asyncio.to_thread(voice.warm_up), asyncio.to_thread(warm_up, domain)):
        task = asyncio.create_task(job)
        _background.add(task)
        task.add_done_callback(_background.discard)
    return {"status": "warming"}


@app.post("/voice/transcribe")
async def voice_transcribe(request: Request, language: str | None = None):
    """Speech → text, on this machine. Body: the recorded audio (webm/opus, mp4, wav, ogg). `language`: ISO code
    to force one (default: detected)."""
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=422, detail="No audio in the request body")
    if len(audio) > 25_000_000:
        raise HTTPException(status_code=413, detail="Audio too long")
    try:
        return await asyncio.to_thread(voice.transcribe, audio, language)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not transcribe: {type(e).__name__}: {e}")


@app.post("/voice/speak")
async def voice_speak(request: SpeakRequest):
    """Text → speech audio (markdown, tables, URLs and uids are left out; the voice follows the language)."""
    try:
        audio, media_type = await asyncio.to_thread(voice.speak, request.text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Speech failed: {type(e).__name__}: {e}")
    return Response(audio, media_type=media_type)


@app.get("/")
async def root():
    return {"status": "Sovereign Hermes is alive"}


@app.get("/health")
async def health():
    return {"hermes": True, "model": HEAD_MODEL, "tracing": TRACING,
            "voice": {"stt": voice.stt_engine(), "tts": voice.tts_engine()}}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8090)
