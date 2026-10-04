"""Ontology routes — read and replace a domain's KG grammar.

Only the Head Agent may change the ontology: it is the only agent given the
update tool. Existing nodes keep their categories; the new grammar applies to
everything ingested afterwards.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from agents.ontologist import bootstrap_domain_ontology
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


@router.post("/{domain_name}/ontology/generate", status_code=202)
async def generate_ontology(domain_name: str, background_tasks: BackgroundTasks):
    """(Re)generate the ontology with the Ontologist in the background, replacing the current one."""
    try:
        config = load_domain(domain_name)["config"]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    background_tasks.add_task(
        bootstrap_domain_ontology, domain_name, config.get("description") or domain_name, overwrite=True
    )
    return {"domain": domain_name, "status": "generating"}
