"""Destination knowledge pack: a cached, versioned bundle of everything known
about a destination (POIs, knowledge, safety, events, weather snapshot) with
provenance and a TTL. If a pack cannot be built, callers fall back to live
retrieval instead of failing."""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.config import CACHE_TTL_PACK_S
from app.db.models import DataSource, Destination, EventFestival, KnowledgeChunk, Poi, SafetyAdvisory
from app.db.session import SessionLocal
from app.knowledge.context import active_advisories, events_for, weather_notices
from app.weather.open_meteo import get_weather, local_now

_packs: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def _version(db, destination_id: str) -> str:
    parts = []
    for model in (Poi, KnowledgeChunk, SafetyAdvisory, EventFestival):
        count, latest = db.execute(select(func.count(), func.max(model.updated_at)).where(model.destination_id == destination_id)).one()
        parts.append(f"{count}:{latest}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def build_pack(destination_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
        if destination is None:
            return None
        pois = db.scalars(select(Poi).where(Poi.destination_id == destination_id)).all()
        chunk_count = db.query(KnowledgeChunk).filter(KnowledgeChunk.destination_id == destination_id).count()
        source = db.get(DataSource, destination.data_source_id) if destination.data_source_id else None
        version = _version(db, destination_id)
    now_local = local_now(destination.timezone)
    weather = get_weather(destination.lat, destination.lon, 0)
    categories: dict[str, int] = {}
    for poi in pois:
        categories[poi.category or "other"] = categories.get(poi.category or "other", 0) + 1
    created = datetime.now(timezone.utc)
    return {
        "destination_id": destination_id,
        "version": version,
        "created_at": created.isoformat(),
        "ttl_s": CACHE_TTL_PACK_S,
        "destination": {"id": destination.id, "name": destination.name, "region": destination.region, "country": destination.country, "lat": destination.lat, "lon": destination.lon, "coverage_radius_km": destination.coverage_radius_km, "timezone": destination.timezone, "currency": destination.currency, "summary": destination.summary, "curated": bool(destination.curated)},
        "counts": {"pois": len(pois), "knowledge_chunks": chunk_count, "categories": categories},
        "poi_ids": [poi.id for poi in pois],
        "advisories": active_advisories(destination_id, now_local.date()) + weather_notices(weather),
        "events": events_for(destination_id, now_local.date()),
        "weather": weather,
        "local_time": now_local.isoformat(timespec="minutes"),
        "provenance": {
            "dataset": source.name if source else destination.source,
            "version": source.version if source else None,
            "license": source.license if source else None,
            "collected_at": source.collected_at if source else None,
            "coverage": source.geographic_coverage if source else None,
            "source_url": source.url if source else destination.source_url,
            "weather": "Open-Meteo (live)",
        },
    }


def get_pack(destination_id: str, force: bool = False) -> dict[str, Any] | None:
    with _lock:
        cached = _packs.get(destination_id)
        if cached and not force and time.monotonic() - cached[0] < CACHE_TTL_PACK_S:
            return {**cached[1], "cached": True}
    pack = build_pack(destination_id)
    if pack is not None:
        with _lock:
            _packs[destination_id] = (time.monotonic(), pack)
    return pack


def invalidate(destination_id: str | None = None) -> None:
    with _lock:
        if destination_id:
            _packs.pop(destination_id, None)
        else:
            _packs.clear()
