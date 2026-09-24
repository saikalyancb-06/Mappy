"""City selection as the initialisation point for destination intelligence."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from app.cities.enrichment import enqueue_enrichment, is_running
from app.cities.registry import city_dict, evaluate_status, get_or_create_city, missing_components
from app.config import CITY_ENRICHMENT_ENABLED
from app.core.rules import load_rules
from app.db.models import Destination, KnowledgeChunk, Poi
from app.db.session import SessionLocal
from app.geo.distance import haversine_km, valid_coordinates
from app.geo.geocoding import resolve_place
from app.models import candidate_from_poi
from app.policy.recommendation import context_from_profile, exclusion_reason

logger = logging.getLogger(__name__)
TRUSTED_KEYS = ("name", "region", "country", "country_code", "timezone")


def _authoritative(payload: dict[str, Any]) -> dict[str, Any]:
    """Client-sent place data is re-resolved server-side before a new city is created."""
    name = " ".join(str(payload.get("name") or "").split())[:120]
    lat, lon = payload.get("lat"), payload.get("lon")
    if not name or not valid_coordinates(lat, lon):
        raise ValueError("Choose a place with a name and coordinates.")
    place = {k: (str(payload[k])[:120] if payload.get(k) is not None else None) for k in TRUSTED_KEYS}
    place.update(name=name, lat=float(lat), lon=float(lon))
    query = f"{name}, {payload['country']}" if payload.get("country") else name
    resolved, _ = resolve_place(query, near=(float(lat), float(lon)))
    if resolved and resolved.kind in {"destination", "geocoded"} and haversine_km(resolved.lat, resolved.lon, float(lat), float(lon)) <= 50:
        if resolved.kind == "destination":
            place["destination_id"] = resolved.destination_id
        else:
            place.update(name=resolved.city or resolved.name, lat=resolved.lat, lon=resolved.lon, region=resolved.region, country=resolved.country,
                         population=resolved.population, boundary_km=resolved.boundary_km, external_ids=resolved.external_ids)
    return place


def ensure_city(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve → get or create the city → start enrichment if anything is missing or stale → return at once."""
    destination_id = payload.get("destination_id") or payload.get("id")
    place = {"destination_id": destination_id} if destination_id else _authoritative(payload)
    if destination_id:
        with SessionLocal() as db:
            if db.get(Destination, destination_id) is None:
                raise ValueError("Unknown destination.")
    city, created = get_or_create_city(place)
    wanted = missing_components(city)
    job_id, enrichment = None, "not_needed"
    if wanted and not CITY_ENRICHMENT_ENABLED:
        enrichment = "disabled"
    elif wanted and is_running(city.id):
        enrichment = "running"
    elif wanted:
        job_id = enqueue_enrichment(city.id)
        enrichment = "started" if job_id else "running"
    with SessionLocal() as db:
        city = db.get(Destination, city.id)
    status = evaluate_status(city)
    logger.info("city_selected destination=%s created=%s status=%s enrichment=%s missing=%s", city.id, created, status["status"], enrichment, ",".join(wanted))
    return {"city": city_dict(city), "created": created, "status": status, "enrichment": enrichment, "job_id": job_id, "missing": wanted}


def city_status(destination_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        city = db.get(Destination, destination_id)
    if city is None:
        return None
    try:
        report = json.loads(city.last_enrichment_report) if city.last_enrichment_report else None
    except ValueError:
        report = None
    return {"city": city_dict(city), "status": evaluate_status(city), "running": is_running(destination_id), "last_run": report}


def city_knowledge(destination_id: str, knowledge_type: str | None = None) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        query = select(KnowledgeChunk).where(KnowledgeChunk.destination_id == destination_id, KnowledgeChunk.kind.in_(["city_kb", "place_kb"]))
        if knowledge_type:
            query = query.where(KnowledgeChunk.category == knowledge_type)
        rows = db.scalars(query.order_by(KnowledgeChunk.kind, KnowledgeChunk.category)).all()
    return [{"id": r.id, "type": r.category, "kind": r.kind, "title": r.title, "content": r.content, "source": r.source, "source_url": r.source_url,
             "last_verified_at": r.updated_at.isoformat() + "Z" if r.updated_at else None} for r in rows]


def city_places(destination_id: str, *, category: str | None = None, kind: str | None = None, profile: dict[str, Any] | None = None, limit: int = 30, offset: int = 0) -> dict[str, Any]:
    """Stored places for a city, best-known first, with the recommendation policy applied."""
    context = context_from_profile(profile)
    with SessionLocal() as db:
        query = select(Poi).where(Poi.destination_id == destination_id)
        if category:
            query = query.where(Poi.category == category)
        if kind:
            query = query.where(Poi.kind == kind)
        rows = db.scalars(query.order_by(Poi.review_count.desc().nullslast(), Poi.rating.desc().nullslast(), Poi.name)).all()
    kept, excluded = [], 0
    for row in rows:
        candidate = candidate_from_poi(row)
        if exclusion_reason(candidate, context):
            excluded += 1
            continue
        kept.append(candidate)
    page = kept[offset: offset + limit]
    return {"items": [c.as_dict() for c in page], "total": len(kept), "excluded_by_policy": excluded, "categories": sorted({c.category for c in kept if c.category}),
            "freshness": load_rules("city_intelligence")["places"]["place_ttl_days"]}
