"""City registry and city intelligence (selection → preparation → local answers).

The frontend only talks to these endpoints; provider details stay on the server.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.cities.enrichment import enqueue_enrichment, is_running
from app.cities.offline import build_offline_pack
from app.cities.registry import components_config, missing_components
from app.cities.service import city_knowledge, city_places, city_status, ensure_city
from app.db.models import Destination
from app.db.session import SessionLocal
from app.events.submissions import is_moderator
from app.services.profile import resolve_profile, user_from_authorization

router = APIRouter(prefix="/api")


@router.post("/destinations/ensure")
def ensure(payload: dict | None) -> dict:
    """Select a city: returns immediately with what is known; enrichment continues in the background."""
    try:
        return ensure_city(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_place", "message": str(exc)}) from None


@router.get("/destinations/{destination_id}/status")
def status(destination_id: str) -> dict:
    found = city_status(destination_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown destination.")
    return found


@router.get("/destinations/{destination_id}/knowledge")
def knowledge(destination_id: str, type: str | None = Query(None, max_length=40)) -> dict:
    if city_status(destination_id) is None:
        raise HTTPException(status_code=404, detail="Unknown destination.")
    return {"items": city_knowledge(destination_id, type)}


@router.get("/destinations/{destination_id}/places")
def places(destination_id: str, category: str | None = Query(None, max_length=40), kind: str | None = Query(None, max_length=20), limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=5000), authorization: str | None = Header(default=None)) -> dict:
    if city_status(destination_id) is None:
        raise HTTPException(status_code=404, detail="Unknown destination.")
    return city_places(destination_id, category=category, kind=kind, profile=resolve_profile(authorization), limit=limit, offset=offset)


@router.get("/destinations/{destination_id}/offline-pack")
def offline_pack(destination_id: str, authorization: str | None = Header(default=None)) -> dict:
    """A compact bundle of the city for offline use (stored on the device by the app)."""
    pack = build_offline_pack(destination_id, resolve_profile(authorization))
    if pack is None:
        raise HTTPException(status_code=404, detail="Unknown destination.")
    return pack


@router.post("/destinations/{destination_id}/enrich")
def enrich(destination_id: str, payload: dict | None = None, authorization: str | None = Header(default=None)) -> dict:
    """Collect missing/stale parts. A full forced refresh (which spends provider quota) is limited to moderators."""
    with SessionLocal() as db:
        city = db.get(Destination, destination_id)
    if city is None:
        raise HTTPException(status_code=404, detail="Unknown destination.")
    safe = payload or {}
    force = bool(safe.get("force"))
    if force and not is_moderator(user_from_authorization(authorization)):
        raise HTTPException(status_code=403, detail={"code": "not_allowed", "message": "Only moderators can force a full refresh."})
    components = [c for c in safe.get("components") or [] if c in components_config()] or None
    if is_running(destination_id):
        return {"job_id": None, "enrichment": "running"}
    if not force and not components and not missing_components(city):
        return {"job_id": None, "enrichment": "not_needed"}
    job_id = enqueue_enrichment(destination_id, force=force, components=components)
    return {"job_id": job_id, "enrichment": "started" if job_id else "running"}
