"""The conversation between a person and the Head, kept in the domain's database — not in the browser, so a reload,
another browser or a full local storage loses nothing.

    ChatTurn  uid, user (the person's account uid), role (user | assistant), content, steps (JSON: what the Head did
              on the way — its words before a tool, the tools it used), status (pending | done | error),
              n (milliseconds: the order), created_at

Hermes writes both sides of a turn as it happens: the question, then a pending answer it completes when the Head is
done — even if the page that asked was closed meanwhile. The UI reads them back (GET /domains/{d}/chat) and waits on
a pending answer. One person's turns are theirs alone; any member of a domain (a viewer too) may talk to its Head.
"""

import time

from core import database as kgdb
from core.database import ArcadeDB

CHAT_JSON = ("steps",)
LIMIT = 400                # turns read back
STALE_SECONDS = 30 * 60    # a pending answer older than this was lost (Hermes restarted): shown as interrupted


def _n() -> int:
    return time.time_ns() // 1_000_000


async def turns(db: ArcadeDB, domain: str, user: str, limit: int = LIMIT) -> list[dict]:
    rows = await kgdb.find_docs(db, domain, "ChatTurn", CHAT_JSON, "user = :user", order="n DESC", limit=limit,
                                user=user)
    now = _n()
    for r in rows:
        if r.get("status") == "pending" and now - (r.get("n") or now) > STALE_SECONDS * 1000:
            r.update(status="error", content=r.get("content") or "The Head was interrupted before it answered.")
    return rows[::-1]


async def add(db: ArcadeDB, domain: str, user: str, role: str, content: str, status: str = "done",
              steps: list | None = None) -> dict:
    return await kgdb.insert_doc(db, domain, "ChatTurn", {
        "user": user, "role": role, "content": content, "status": status, "steps": steps or [], "n": _n()}, CHAT_JSON)


async def update(db: ArcadeDB, domain: str, user: str, uid: str, changes: dict) -> bool:
    turn = await kgdb.get_doc(db, domain, "ChatTurn", uid, CHAT_JSON)
    if not turn or turn["user"] != user:
        return False
    allowed = {k: v for k, v in changes.items() if k in ("content", "status", "steps") and v is not None}
    return await kgdb.update_doc(db, domain, "ChatTurn", uid, allowed, CHAT_JSON) if allowed else True


async def clear(db: ArcadeDB, domain: str, user: str) -> None:
    if await db.exists(domain):
        await db.sql(domain, "DELETE FROM ChatTurn WHERE user = :user", user=user)


async def import_turns(db: ArcadeDB, domain: str, user: str, items: list[dict]) -> int:
    """A browser's old local conversation, moved here once — only into an empty one."""
    if await kgdb.find_docs(db, domain, "ChatTurn", where="user = :user", limit=1, user=user):
        return 0
    base = _n() - len(items)
    for i, t in enumerate(items):
        await kgdb.insert_doc(db, domain, "ChatTurn", {
            "user": user, "role": t["role"], "content": t["content"], "status": "error" if t.get("error") else "done",
            "steps": t.get("steps") or [], "n": base + i}, CHAT_JSON)
    return len(items)
