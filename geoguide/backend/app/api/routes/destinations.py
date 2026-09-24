from __future__ import annotations

import threading
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.api.common import destination_from_params, geo_for, location_from_params
from app.db.models import IngestionJob, utcnow
from app.db.session import SessionLocal
from app.geo.city import resolve_city
from app.geo.geo_context import parse_user_location
from app.geo.geocoding import reverse_geocode, resolve_place, search_destinations
from app.ingestion.overpass import fetch_and_store
from app.knowledge.destination_pack import get_pack
from app.weather.open_meteo import get_weather

router = APIRouter(prefix="/api")


@router.get("/destinations")
def list_destinations(q: str | None = None, limit: int = Query(10, ge=1, le=50)) -> dict:
    return {"items": search_destinations(q, limit)}


@router.get("/destinations/resolve")
def resolve_destination(q: str = Query(..., min_length=2, max_length=120)) -> dict:
    place, errors = resolve_place(q)
    if place is None:
        detail = {"code": "not_found", "message": f"“{q}” could not be found."}
        if errors:
            detail = {"code": "provider_unavailable", "message": "The place search service is unavailable right now, and the place is not in stored data.", "errors": errors}
        raise HTTPException(status_code=404, detail=detail)
    return {"place": place.as_dict(), "provider_errors": errors}


@router.get("/destinations/{destination_id}/pack")
def destination_pack(destination_id: str, refresh: bool = False) -> dict:
    pack = get_pack(destination_id, force=refresh)
    if pack is None:
        raise HTTPException(status_code=404, detail="Destination not found.")
    return pack


@router.get("/location/describe")
def describe_location(lat: float, lon: float, accuracy_m: float | None = None, timestamp: str | None = None) -> dict:
    """Name the traveller's physical area and whether it lies inside a curated destination."""
    location, status, warnings = parse_user_location(location_from_params(lat, lon, accuracy_m, timestamp))
    if location is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_location", "message": warnings[0] if warnings else "Invalid location."})
    named, errors = reverse_geocode(location.lat, location.lon)
    city, city_errors = resolve_city(lat=location.lat, lon=location.lon)
    detected = None
    if city is not None:
        detected = {"name": city.name, "destination_id": city.destination_id, "region": city.region, "country": city.country, "resolved_by": city.resolved_by}
        if city.destination_id:
            detected.update(lat=city.lat, lon=city.lon, kind="destination")
    return {"location_status": status, "warnings": warnings, "area": named, "city": detected, "inside_destination_id": location.inside_destination_id, "provider_errors": [*errors, *city_errors]}


def _run_prefetch(job_id: str, lat: float, lon: float, radius_km: float) -> None:
    def update(**values) -> None:
        with SessionLocal() as db:
            job = db.get(IngestionJob, job_id)
            if job:
                for key, value in values.items():
                    setattr(job, key, value)
                job.updated_at = utcnow()
                db.commit()

    update(status="running", step="collect_places", progress=30)
    count, error = fetch_and_store(lat, lon, radius_km)
    if error:
        update(status="failed", step="collect_places", progress=100, error=error["message"])
        return
    update(step="weather", progress=80)
    get_weather(lat, lon, 0)
    update(status="completed", step="done", progress=100, error=None)


@router.post("/destinations/prefetch")
def prefetch(payload: dict | None) -> dict:
    """Warm the stores for a non-curated place (OpenStreetMap POIs + weather) in the background."""
    safe = payload or {}
    geo = geo_for("auto", location_from_params(safe.get("lat"), safe.get("lon"), safe.get("accuracy_m"), safe.get("timestamp")), destination_from_params(safe.get("destination_id"), safe.get("name")))
    reference = geo.reference
    job_id = uuid.uuid4().hex
    if reference.destination_id:
        return {"job_id": None, "status": "completed", "message": "Curated destination data is already available.", "destination_id": reference.destination_id}
    with SessionLocal() as db:
        db.add(IngestionJob(id=job_id, destination_id=None, status="queued", step="queued", progress=5))
        db.commit()
    threading.Thread(target=_run_prefetch, args=(job_id, reference.lat, reference.lon, min(reference.radius_km, 8.0)), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/destinations/prefetch/{job_id}")
def prefetch_status(job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return {"job_id": job.id, "status": job.status, "step": job.step, "progress": job.progress, "error": job.error}


@router.get("/weather")
def weather(lat: float | None = None, lon: float | None = None, destination_id: str | None = None, destination: str | None = None, day_offset: int = Query(0, ge=0, le=6)) -> dict:
    if lat is not None and lon is not None:
        point, label = (lat, lon), "the selected point"
    else:
        geo = geo_for("destination", None, destination_from_params(destination_id, destination))
        point, label = (geo.reference.lat, geo.reference.lon), geo.reference.label
    return {"place": label, **get_weather(point[0], point[1], day_offset)}
