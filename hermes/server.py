"""Hermes service — HTTP entry point for talking to the Head Agent.

    uv run python server.py   → http://localhost:8090

The frontend's Ask panel calls POST /domains/{domain}/ask/stream here (SSE). The KG API
(port 8080) must be running: the Head Agent reaches the KG through its MCP server.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import auth
import voice
from head_agent import HEAD_MODEL, TRACING, DomainNotFound, KGUnavailable, Stop, ask_head, scrub, warm_up


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
    # The page sends its whole conversation (it keeps 400 turns); the Head reads only the newest (head_agent.recent).
    history: list[Turn] = Field(default=[], max_length=500)
    max_iterations: int = Field(default=8, ge=1, le=30)
    voice: bool = Field(default=False, description="Spoken conversation: short answers meant to be read aloud")


# ── The conversation, kept in the KG (kg/core/chat.py) ──────────────────────
# A person's question is saved when it arrives and the Head's answer when it is done — from here, not from the page,
# so an answer still lands if the page was reloaded or closed while the Head worked.

def _chat_open(domain: str, user: str | None, message: str) -> tuple[str, str] | None:
    """Save the question and a pending answer; (user, answer uid), or None (a service caller, or the KG said no)."""
    if not user:
        return None
    try:
        with httpx.Client(base_url=auth.KG_URL, timeout=10, headers=auth.headers(domain)) as kg:
            kg.post(f"/domains/{domain}/chat", json={"role": "user", "content": message, "user": user}).raise_for_status()
            r = kg.post(f"/domains/{domain}/chat", json={"role": "assistant", "status": "pending", "user": user})
            r.raise_for_status()
            return user, r.json()["uid"]
    except httpx.HTTPError as e:
        print(f"[Hermes] conversation not saved for {domain}: {type(e).__name__}", flush=True)
        return None


def _chat_close(domain: str, handle: tuple[str, str] | None, content: str, steps: list, status: str) -> None:
    if not handle:
        return
    try:
        with httpx.Client(base_url=auth.KG_URL, timeout=10, headers=auth.headers(domain)) as kg:
            kg.patch(f"/domains/{domain}/chat/{handle[1]}",
                     json={"content": content, "steps": steps, "status": status, "user": handle[0]}).raise_for_status()
    except httpx.HTTPError as e:
        print(f"[Hermes] answer not saved for {domain}: {type(e).__name__}", flush=True)


class _Steps:
    """What the Head did on the way, as the Ask panel shows it: its words before a tool, the tools, the actions."""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.said = ""

    def record(self, e: dict | None) -> None:
        if not e:
            return
        if e["type"] == "delta":
            self.said += e["text"]
        elif e["type"] == "tool_start":
            if self.said.strip():
                self.steps.append({"id": f"said-{len(self.steps)}", "name": "", "args": None, "done": True,
                                   "said": scrub(self.said).strip()})
            self.said = ""
            args = e["args"] if isinstance(e["args"], dict) else {}
            self.steps.append({"id": e["id"], "name": e["name"], "done": False,
                               "args": {k: str(args[k])[:200] for k in ("query", "raw_text", "action") if k in args}})
        elif e["type"] == "tool_end":
            for s in self.steps:
                if s["id"] == e["id"]:
                    s["done"] = True
                    if e["name"] == "propose_action":
                        try:
                            p = json.loads(e["result"])
                            t = (p.get("params") or {}).get("title") or (p.get("params") or {}).get("message") or ""
                            s["outcome"] = {"action": p["action"], "status": p["status"], "title": str(t)}
                        except (ValueError, KeyError, TypeError, AttributeError):
                            pass


def _user() -> str | None:
    p = auth.current.get()
    return p.user["uid"] if p and p.kind == "user" and p.user else None


@app.post("/domains/{domain_name}/ask")
async def ask(domain_name: str, request: AskRequest):
    """One turn with the Head Agent, grounded in the domain's KG.

    Hermes's orchestration loop is synchronous, so it runs in a worker thread.
    """
    history = [t.model_dump() for t in request.history]
    handle = await asyncio.to_thread(_chat_open, domain_name, _user(), request.message)
    try:
        answer = await asyncio.to_thread(
            ask_head, domain_name, request.message, history, max_iterations=request.max_iterations,
            voice=request.voice, tools=_tools(domain_name),
        )
    except DomainNotFound:
        await asyncio.to_thread(_chat_close, domain_name, handle, "This domain does not exist.", [], "error")
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' does not exist")
    except KGUnavailable as e:
        await asyncio.to_thread(_chat_close, domain_name, handle, str(e), [], "error")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        await asyncio.to_thread(_chat_close, domain_name, handle, f"Head Agent failed: {type(e).__name__}", [], "error")
        raise HTTPException(status_code=500, detail=f"Head Agent failed: {type(e).__name__}: {e}")
    await asyncio.to_thread(_chat_close, domain_name, handle, answer, [], "done")
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
    user = _user()
    handle = await asyncio.to_thread(_chat_open, domain_name, user, request.message)
    log = _Steps()
    # Stop (POST …/ask/stop) stops the Head: until 2026-10-14 the turn ran on in its thread — its ingests, proposals,
    # review decisions and tasks still happened. A closed stream does NOT stop it: a reload, another domain or a
    # closed tab must still get the answer (user, 2026-10-13: "messages must survive a reload"; the page picks up a
    # pending answer from the KG).
    stop = Stop()

    def emit(event: dict | None) -> None:  # called from the agent's worker thread
        log.record(event)
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def stopped() -> str:
        """A stopped turn is saved as an error: what the Head had said so far (its earlier words are in the steps)."""
        return f"{scrub(log.said).strip()}\n\nStopped.".strip()

    def run() -> None:
        content, status = "", "error"
        try:
            answer = ask_head(
                domain_name,
                request.message,
                [t.model_dump() for t in request.history],
                max_iterations=request.max_iterations,
                on_event=emit,
                voice=request.voice,
                tools=tools,
                stop=stop,
            )
            if stop.stopped:
                content = stopped()
                emit({"type": "error", "detail": content})
            else:
                content, status = answer or log.said.strip() or "(no answer)", "done"
                emit({"type": "answer", "text": answer})
        except DomainNotFound:
            content = f"Domain '{domain_name}' does not exist"
            emit({"type": "error", "detail": content})
        except KGUnavailable as e:
            content = str(e)
            emit({"type": "error", "detail": content})
        except Exception as e:
            content = stopped() if stop.stopped else f"Head Agent failed: {type(e).__name__}: {e}"
            emit({"type": "error", "detail": content})
        finally:
            # Saved even if the page that asked is gone: the worker thread outlives the stream.
            _chat_close(domain_name, handle, content, log.steps, status)
            emit(None)

    turns = _running.setdefault((domain_name, user), set())
    turns.add(stop)
    worker = asyncio.create_task(asyncio.to_thread(run))
    worker.add_done_callback(lambda _t: turns.discard(stop))

    async def events():
        while (event := await queue.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"
        await worker

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


_running: dict[tuple[str, str | None], set[Stop]] = {}   # (domain, person) → their turns still running


@app.post("/domains/{domain_name}/ask/stop")
async def ask_stop(domain_name: str):
    """Stop the caller's running turns with this domain's Head (the Stop button). Hermes' interrupt: no tool call
    starts after it; one already running finishes. The turn is saved as stopped."""
    stops = list(_running.get((domain_name, _user()), ()))
    for stop in stops:
        stop()
    return {"stopped": len(stops)}


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
