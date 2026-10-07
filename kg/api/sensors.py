"""Sensors — live-data tools: groups of sources (APIs and web pages) found by the Scout, run in the sandbox."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import auth, coding, scout, sensors
from domains.registry import load_domain

router = APIRouter(tags=["sensors"])


def _require(domain: str) -> None:
    try:
        load_domain(domain)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class SensorRequest(BaseModel):
    need: str = Field(min_length=5, description='What to sense, e.g. "latest stock price and day change for a ticker"')
    backend: Literal["scout", "builtin", "openhands"] | None = None
    group: str | None = Field(default=None, description="Scout: find more sources for this existing sensor")


class ReadRequest(BaseModel):
    params: dict = Field(default_factory=dict)


@router.get("/domains/{domain_name}/sensors")
async def list_sensors(domain_name: str, req: Request):
    _require(domain_name)
    db = req.app.state.db
    return {"groups": await sensors.groups(db, domain_name), "sensors": await sensors.list_sensors(db, domain_name),
            "requests": coding.jobs(domain_name)[:20]}


@router.get("/domains/{domain_name}/sensors/{name}")
async def get_sensor(domain_name: str, name: str, req: Request):
    """One sensor with its code (review what runs in your name)."""
    _require(domain_name)
    if not (sensor := await sensors.get_sensor(req.app.state.db, domain_name, name)):
        raise HTTPException(status_code=404, detail=f"No sensor {name!r}")
    return sensor


@router.post("/domains/{domain_name}/sensors/{name}/read")
async def read_sensor(domain_name: str, name: str, request: ReadRequest, req: Request):
    _require(domain_name)
    try:
        return await sensors.read(req.app.state.db, domain_name, name, request.params)
    except sensors.SensorError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/domains/{domain_name}/sensors/{name}")
async def delete_sensor(domain_name: str, name: str, req: Request):
    _require(domain_name)
    try:
        await sensors.remove(req.app.state.db, domain_name, name)
    except sensors.SensorError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "deleted", "name": name}


@router.post("/domains/{domain_name}/sensors", status_code=202)
async def request_sensor(domain_name: str, request: SensorRequest, req: Request):
    """Ask for a sensor (minutes): by default the Scout tries many sources and keeps every one that works; with
    `group`, it looks for more sources for that sensor. Poll /sensor-requests/{id}."""
    _require(domain_name)
    if request.group and not await sensors.members(req.app.state.db, domain_name, request.group, with_code=False):
        raise HTTPException(status_code=404, detail=f"No sensor {request.group!r}")
    backend = "scout" if request.group else request.backend
    return coding.submit(req.app.state.db, domain_name, request.need, backend, group=request.group)


@router.post("/domains/{domain_name}/sensors/check")
async def check_sensors(domain_name: str, req: Request):
    """Read every source of every sensor once; sensors with fewer than two working sources get scouted again.
    Runs every night before the report."""
    _require(domain_name)
    return {"sensors": await scout.health_check(req.app.state.db, domain_name)}


@router.get("/sensor-requests/{job_id}")
async def sensor_request(job_id: str):
    j = coding.job(job_id)
    if not j or not auth.current.get().can(j["domain"]):
        raise HTTPException(status_code=404, detail=f"No sensor request {job_id}")
    return j
