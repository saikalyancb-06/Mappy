"""Place-name resolution.

Order: curated destinations and their aliases → known POIs (landmarks) →
Nominatim (cached). An unresolvable name returns ``None``; there is no
default city and no fabricated coordinate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from app.config import CACHE_TTL_GEOCODE_S, NOMINATIM_URL
from app.core.cache import cache_get, cache_set
from app.core.http import ProviderError, get_json
from app.core.text import name_similarity, normalize
from app.db.models import Destination, EntityAlias, Poi
from app.db.session import SessionLocal
from app.geo.distance import haversine_km, valid_coordinates

logger = logging.getLogger(__name__)

DESTINATION_MATCH_THRESHOLD = 0.86
POI_MATCH_THRESHOLD = 0.82


@dataclass
class ResolvedPlace:
    name: str
    lat: float
    lon: float
    kind: str  # destination | poi | geocoded
    city: str | None = None
    region: str | None = None
    country: str | None = None
    destination_id: str | None = None
    poi_id: str | None = None
    coverage_radius_km: float | None = None
    timezone: str | None = None
    source: str = "database"
    confidence: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def destination_to_place(destination: Destination, confidence: float = 1.0, source: str = "database") -> ResolvedPlace:
    return ResolvedPlace(
        name=destination.name,
        lat=destination.lat,
        lon=destination.lon,
        kind="destination",
        city=destination.name,
        region=destination.region,
        country=destination.country,
        destination_id=destination.id,
        coverage_radius_km=destination.coverage_radius_km,
        timezone=destination.timezone,
        source=source,
        confidence=confidence,
    )


def _match_destination(name: str) -> tuple[Destination, float] | None:
    target = normalize(name)
    if not target:
        return None
    best: tuple[Destination, float] | None = None
    with SessionLocal() as db:
        destinations = {d.id: d for d in db.scalars(select(Destination)).all()}
        aliases = db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "destination")).all()
    candidates: list[tuple[str, str]] = [(d.id, d.name) for d in destinations.values()] + [(a.entity_id, a.alias) for a in aliases]
    for destination_id, label in candidates:
        score = name_similarity(target, label)
        destination = destinations.get(destination_id)
        # Qualified names ("<name>, <region>, <country>") still match their destination.
        label_norm = normalize(label)
        if destination and label_norm and target.startswith(label_norm + " "):
            qualifier = set(target[len(label_norm):].split())
            allowed = set(normalize(f"{destination.region or ''} {destination.country or ''} {destination.country_code or ''}").split())
            score = max(score, 0.95) if qualifier and qualifier <= allowed else min(score, 0.8)
        if destination_id in destinations and (best is None or score > best[1]):
            best = (destinations[destination_id], score)
    return best if best and best[1] >= DESTINATION_MATCH_THRESHOLD else None


def _match_poi(name: str, near: tuple[float, float] | None = None) -> list[tuple[Poi, float]]:
    target = normalize(name)
    if not target:
        return []
    first = target.split()[0]
    with SessionLocal() as db:
        pois = db.scalars(select(Poi).where(Poi.normalized_name.contains(first))).all()
        alias_rows = db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "poi", EntityAlias.normalized.contains(first))).all()
        alias_pois = {row.entity_id: row.alias for row in alias_rows}
        if alias_pois:
            extra = db.scalars(select(Poi).where(Poi.id.in_(list(alias_pois)))).all()
            pois = list({p.id: p for p in [*pois, *extra]}.values())
    scored = []
    for poi in pois:
        score = max(name_similarity(target, poi.name), name_similarity(target, alias_pois.get(poi.id, "")))
        if near:
            # Prefer the landmark closest to the current reference when names tie.
            score -= min(0.05, haversine_km(near[0], near[1], poi.lat, poi.lon) / 2000)
        if score >= POI_MATCH_THRESHOLD:
            scored.append((poi, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _nominatim_search(name: str) -> dict[str, Any] | None:
    key = normalize(name)
    cached = cache_get("geocode", key)
    if cached is not None:
        return cached["data"]
    payload = get_json("nominatim", f"{NOMINATIM_URL}/search", params={"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1}, timeout=6.0)
    item = payload[0] if isinstance(payload, list) and payload else None
    result = None
    if item and valid_coordinates(item.get("lat"), item.get("lon")):
        address = item.get("address") or {}
        result = {
            "name": address.get("city") or address.get("town") or address.get("village") or item.get("name") or name,
            "display_name": item.get("display_name"),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "region": address.get("state"),
            "country": address.get("country"),
            "importance": item.get("importance"),
            "type": item.get("type"),
        }
    cache_set("geocode", key, result, CACHE_TTL_GEOCODE_S, source="nominatim")
    return result


def resolve_place(name: str, near: tuple[float, float] | None = None, allow_remote: bool = True) -> tuple[ResolvedPlace | None, list[dict[str, str]]]:
    """Resolve a free-text place mention. Returns (place or None, provider errors)."""
    errors: list[dict[str, str]] = []
    if not normalize(name):
        return None, errors
    destination_match = _match_destination(name)
    poi_matches = _match_poi(name, near)
    if destination_match and (not poi_matches or destination_match[1] >= poi_matches[0][1]):
        return destination_to_place(destination_match[0], confidence=round(destination_match[1], 3)), errors

    if poi_matches:
        poi, score = poi_matches[0]
        alternatives = [{"poi_id": p.id, "name": p.name, "score": round(s, 3)} for p, s in poi_matches[1:4]]
        with SessionLocal() as db:
            destination = db.get(Destination, poi.destination_id) if poi.destination_id else None
        return ResolvedPlace(
            name=poi.name,
            lat=poi.lat,
            lon=poi.lon,
            kind="poi",
            city=destination.name if destination else None,
            region=destination.region if destination else None,
            country=destination.country if destination else None,
            destination_id=poi.destination_id,
            poi_id=poi.id,
            coverage_radius_km=destination.coverage_radius_km if destination else None,
            timezone=destination.timezone if destination else None,
            source=poi.source or "database",
            confidence=round(score, 3),
            alternatives=alternatives,
        ), errors

    if not allow_remote:
        return None, errors
    try:
        found = _nominatim_search(name)
    except ProviderError as exc:
        errors.append(exc.as_dict())
        return None, errors
    if not found:
        return None, errors
    destination = nearest_destination(found["lat"], found["lon"])
    return ResolvedPlace(
        name=found["name"],
        lat=found["lat"],
        lon=found["lon"],
        kind="geocoded",
        city=found["name"],
        region=found.get("region"),
        country=found.get("country"),
        destination_id=destination.id if destination else None,
        coverage_radius_km=destination.coverage_radius_km if destination else None,
        timezone=destination.timezone if destination else None,
        source="nominatim",
        confidence=0.7,
    ), errors


def nearest_destination(lat: float, lon: float) -> Destination | None:
    """The curated destination whose coverage area contains the point, if any."""
    with SessionLocal() as db:
        destinations = db.scalars(select(Destination)).all()
    containing = [(haversine_km(lat, lon, d.lat, d.lon), d) for d in destinations if haversine_km(lat, lon, d.lat, d.lon) <= (d.coverage_radius_km or 0)]
    return min(containing, key=lambda item: item[0])[1] if containing else None


def reverse_geocode(lat: float, lon: float) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Name the area around a coordinate: curated destination first, then Nominatim."""
    destination = nearest_destination(lat, lon)
    if destination:
        return {"name": destination.name, "region": destination.region, "country": destination.country, "destination_id": destination.id, "source": "database"}, []
    key = f"{lat:.3f},{lon:.3f}"
    cached = cache_get("reverse_geocode", key)
    if cached is not None:
        return cached["data"], []
    try:
        payload = get_json("nominatim", f"{NOMINATIM_URL}/reverse", params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14, "addressdetails": 1}, timeout=5.0)
    except ProviderError as exc:
        return None, [exc.as_dict()]
    address = (payload or {}).get("address") or {}
    name = address.get("suburb") or address.get("city") or address.get("town") or address.get("village") or address.get("county")
    result = {"name": name, "city": address.get("city") or address.get("town") or address.get("village"), "region": address.get("state"), "country": address.get("country"), "destination_id": None, "source": "nominatim"} if name else None
    cache_set("reverse_geocode", key, result, CACHE_TTL_GEOCODE_S, source="nominatim")
    return result, []


def search_destinations(query: str | None, limit: int = 10) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        destinations = db.scalars(select(Destination)).all()
        poi_counts = {d.id: db.query(Poi).filter(Poi.destination_id == d.id).count() for d in destinations}
    rows = []
    for destination in destinations:
        score = name_similarity(query, destination.name) if query else 1.0
        languages = json.loads(destination.languages or "[]")
        rows.append((score, {"id": destination.id, "name": destination.name, "region": destination.region, "country": destination.country, "lat": destination.lat, "lon": destination.lon, "coverage_radius_km": destination.coverage_radius_km, "timezone": destination.timezone, "languages": languages, "curated": bool(destination.curated), "poi_count": poi_counts.get(destination.id, 0), "summary": destination.summary}))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [row for score, row in rows if not query or score >= 0.5][:limit]
