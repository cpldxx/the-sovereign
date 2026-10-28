"""Domain management routes."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from agents.ontologist import bootstrap_domain_ontology
from core import accounts, auth, digest, kg
from core.accounts import AccountError
from core.database import domain_stats, drop_domain_db, ensure_domain_db
from domains.registry import create_domain, delete_domain, list_domains, load_domain, ontology_path

router = APIRouter(prefix="/domains", tags=["domains"])


class CreateDomainRequest(BaseModel):
    name: str = Field(..., description="Display name; the domain id is derived from it ('Quant Trading' -> 'quant_trading')")
    description: str
    data_sources: list[str] = []
    keywords: list[str] = []


@router.post("")
async def api_create_domain(request: CreateDomainRequest, background_tasks: BackgroundTasks, req: Request):
    """Create a new domain workspace + its ArcadeDB database (the caller owns it), then generate the ontology in
    the background. The id is the name's slug, with a short suffix if another account's domain has it."""
    principal = auth.current.get()
    if principal.kind != "user":
        raise HTTPException(status_code=403, detail="Domains are created by people (sign in)")
    db = req.app.state.db
    avoid: set[str] = set()
    while True:
        try:
            # Only the caller's own domains: one shared with them must not block their own of the same name.
            domain = create_domain(request.name, request.description, request.data_sources, request.keywords,
                                   mine={d for d, r in principal.roles.items() if r == "owner"}, avoid=avoid)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            if not await db.exists(domain):
                break
        except Exception as e:
            delete_domain(domain)
            raise HTTPException(status_code=500, detail=f"Domain database creation failed: {e}")
        # A database under this id without a folder: a leftover (a delete that failed half-way) — or a domain whose
        # folder is elsewhere (SOVEREIGN_DOMAINS_DIR changed). A new domain must not adopt it, and dropping it could
        # destroy a real graph: take another id.
        print(f"[domains] a database '{domain}' exists without a folder — the new domain takes another id", flush=True)
        delete_domain(domain)
        avoid.add(domain)

    try:
        # Memberships under this id without a folder or a database are leftovers: a new domain starts with no members.
        await accounts.forget_domain(domain)
        await ensure_domain_db(db, domain)
        await accounts.add_member(domain, principal.user["uid"], "owner", added_by=principal.user["uid"])
    except Exception as e:
        delete_domain(domain)
        try:
            await drop_domain_db(db, domain)
        except Exception:
            pass   # a database left behind makes the next domain under this id take another one
        raise HTTPException(status_code=500, detail=f"Domain database creation failed: {e}")

    background_tasks.add_task(bootstrap_domain_ontology, domain, request.description)
    return {"status": "created", "domain": domain}


def _summary(domain: str, role: str | None) -> dict:
    config = load_domain(domain)["config"]
    return {
        "id": domain,
        "title": config.get("title") or domain,
        "description": config.get("description", ""),
        "ontology_generated": ontology_path(domain).exists(),
        "role": role,
    }


@router.get("")
async def api_list_domains():
    """The caller's domains: id, title, description, whether the ontology has been generated yet, and the
    caller's role there."""
    principal = auth.current.get()
    return {"domains": [_summary(d, principal.role(d)) for d in principal.visible(list_domains())]}


@router.get("/{domain_name}")
async def api_get_domain(domain_name: str, req: Request):
    """Domain config, ontology status, and KG stats (node/edge counts, nodes per category)."""
    try:
        domain = load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "domain": domain_name,
        "config": domain["config"],
        "ontology_generated": ontology_path(domain_name).exists(),
        "stats": await domain_stats(req.app.state.db, domain_name),
        "role": auth.current.get().role(domain_name),
    }


@router.delete("/{domain_name}")
async def api_delete_domain(domain_name: str, req: Request):
    """Delete a domain workspace and drop its ArcadeDB database (owners). The database goes first, then the
    memberships, then the folder: a failed drop leaves the domain listed and owned, so deleting it can be tried again
    (2026-10-14: the folder went first — a failed drop left a database nobody could see or delete)."""
    try:
        ontology_path(domain_name)   # the folder exists (its config may be broken: still deletable)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for step, run in (("dropping its database", lambda: drop_domain_db(req.app.state.db, domain_name)),
                      ("forgetting its members", lambda: accounts.forget_domain(domain_name))):
        try:
            await run()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Deleting '{domain_name}' failed at {step}: {e}")
    try:
        delete_domain(domain_name)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Deleting '{domain_name}' failed at removing its folder: {e}")
    return {"status": "deleted", "domain": domain_name}


# ── Sharing ────────────────────────────────────────────────────────────────

class ShareRequest(BaseModel):
    email: str = Field(max_length=254)
    role: Literal["owner", "editor", "viewer"]


@router.get("/{domain_name}/members")
async def api_members(domain_name: str):
    """Who can use this domain, and as what: viewer (reads, asks), editor (also writes: ingests, researches, reviews),
    owner (also shares, deletes)."""
    return {"members": await accounts.members(domain_name)}


@router.put("/{domain_name}/members")
async def api_share(domain_name: str, request: ShareRequest):
    """Give an existing account a role here, or change it (owners)."""
    principal = auth.current.get()
    try:
        return {"members": await accounts.share(domain_name, request.email, request.role,
                                                by=principal.user["uid"] if principal.user else "service")}
    except AccountError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.delete("/{domain_name}/members/{uid}")
async def api_unshare(domain_name: str, uid: str):
    """Remove a member (owners) — or leave the domain yourself."""
    principal = auth.current.get()
    if not (principal.can(domain_name, "owner") or (principal.user and principal.user["uid"] == uid)):
        raise HTTPException(status_code=403, detail=f"Only an owner can remove others from '{domain_name}'")
    try:
        await accounts.remove_member(domain_name, uid)
    except AccountError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return {"members": await accounts.members(domain_name)}


@router.post("/{domain_name}/prune")
async def api_prune(domain_name: str, req: Request, dry_run: bool = True):
    """Entities no fact touches any more (those a pending review refers to stay). dry_run (the
    default) only lists them; the nightly report prunes them by itself. Owner only."""
    auth.require(domain_name, "owner")
    gone = await digest.prune_entities(req.app.state.db, domain_name, dry_run)
    return {"dry_run": dry_run, "count": len(gone), "entities": gone}


@router.post("/{domain_name}/relink")
async def api_relink(domain_name: str, req: Request, dry_run: bool = True, unconnected_only: bool = True):
    """Stored has_state facts that name another entity → the relations they state, added as checked facts (the
    states stay). dry_run (the default) only lists them. One model call per ten statements. Owner only."""
    auth.require(domain_name, "owner")
    try:
        return await kg.relink_states(req.app.state.db, domain_name, dry_run, unconnected_only=unconnected_only)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{domain_name}/recheck-superseded")
async def api_recheck_superseded(domain_name: str, req: Request, dry_run: bool = True):
    """Superseded facts asked of the Linker again, beside the fact that replaced them; the ones it finds still true
    are valid again. dry_run (the default) only lists them. One model call per ten new facts. Owner only."""
    auth.require(domain_name, "owner")
    return await kg.recheck_superseded(req.app.state.db, domain_name, dry_run)
