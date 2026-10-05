"""Ontology routes — read and replace a domain's KG grammar.

Only the Head Agent may change the ontology: it is the only agent given the
update tool. Existing nodes keep their categories; the new grammar applies to
everything ingested afterwards.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from agents.ontologist import bootstrap_domain_ontology
from agents.ontologist import generate_ontology as generate_ontology_now
from core.ontology import Ontology, save_ontology
from domains.registry import load_domain, ontology_path

router = APIRouter(prefix="/domains", tags=["ontology"])


@router.get("/{domain_name}/ontology")
async def get_ontology(domain_name: str):
    """Current ontology. `generated` is false while the domain still uses the default grammar."""
    try:
        ontology = load_domain(domain_name)["ontology"]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "domain": domain_name,
        "generated": ontology_path(domain_name).exists(),
        "ontology": ontology,
    }


@router.put("/{domain_name}/ontology")
async def put_ontology(domain_name: str, ontology: Ontology):
    """Replace the ontology. Type names are normalized ('Price Pattern' -> 'price_pattern')."""
    try:
        load_domain(domain_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    save_ontology(domain_name, ontology)
    return {"domain": domain_name, "generated": True, "ontology": ontology.model_dump()}


class GenerateRequest(BaseModel):
    corpus: list[str] = Field(default_factory=list, description="Texts from real sources to ground the grammar in")
    wait: bool = Field(default=False, description="Generate synchronously and return the ontology")


@router.post("/{domain_name}/ontology/generate", status_code=202)
async def generate_ontology(domain_name: str, background_tasks: BackgroundTasks, request: GenerateRequest | None = None):
    """(Re)generate the ontology with the Ontologist, replacing the current one.

    With `corpus` (e.g. pages from the research bootstrap) the grammar is derived from real sources.
    By default it runs in the background; `wait: true` returns the new ontology (or 502 on failure).
    """
    try:
        config = load_domain(domain_name)["config"]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    request = request or GenerateRequest()
    description = config.get("description") or domain_name
    if request.wait:
        try:
            ontology = await generate_ontology_now(domain_name, description, request.corpus or None)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ontology generation failed: {type(e).__name__}: {e}")
        save_ontology(domain_name, ontology)
        return {"domain": domain_name, "status": "generated", "ontology": ontology.model_dump()}
    background_tasks.add_task(
        bootstrap_domain_ontology, domain_name, description, overwrite=True, corpus=request.corpus or None
    )
    return {"domain": domain_name, "status": "generating"}
