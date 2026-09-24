"""Place-name resolution.

Order: curated destinations and their aliases → known POIs (landmarks) →
Nominatim (cached). An unresolvable name returns ``None``; there is no
default city and no fabricated coordinate.

Nominatim is biased to the traveller: a name is looked up near the current
reference first. A far-away match is accepted only when it is a settlement or
a well-known place, and generic names ("city centre", "bus stand") are never
looked up worldwide, so a query in Bengaluru cannot land in another country
because some district there happens to be called "City Centre".
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from app.config import CACHE_TTL_GEOCODE_S, NOMINATIM_URL
from app.core.cache import cache_get, cache_set
from app.core.http import ProviderError, get_json
from app.core.rules import load_rules
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


def destination_aliases(destination: Destination) -> list[str]:
    """Other common names for a destination (data/config/geo.json)."""
    return list(load_rules("geo")["destination_aliases"].get(destination.name, []))


def city_extent_km(destination: Destination) -> float:
    """How far from its centre a point still belongs to this city, for location detection.

    ``coverage_radius_km`` only describes how far the stored places spread; a large city
    extends well beyond that, so the extent grows with population (capped).
    """
    rules = load_rules("geo")["city_extent"]
    coverage = float(destination.coverage_radius_km or 0)
    population = float(getattr(destination, "population", 0) or 0)
    by_population = rules["km_per_sqrt_million"] * (population / 1_000_000) ** 0.5
    return max(coverage, min(float(rules["max_km"]), by_population))


def is_generic_name(name: str) -> bool:
    """True when every word is generic ("city centre", "the bus stand"): not a unique place."""
    words = normalize(name).split()
    generic = {normalize(w) for w in load_rules("geo")["generic_words"]}
    return bool(words) and all(word in generic for word in words)


def _match_destination(name: str) -> tuple[Destination, float] | None:
    target = normalize(name)
    if not target:
        return None
    best: tuple[Destination, float] | None = None
    with SessionLocal() as db:
        destinations = {d.id: d for d in db.scalars(select(Destination)).all()}
        aliases = db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "destination")).all()
    candidates: list[tuple[str, str]] = [(d.id, d.name) for d in destinations.values()] + [(a.entity_id, a.alias) for a in aliases]
    candidates += [(d.id, alias) for d in destinations.values() for alias in destination_aliases(d)]
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


def _nominatim_result(item: dict[str, Any], name: str) -> dict[str, Any] | None:
    if not item or not valid_coordinates(item.get("lat"), item.get("lon")):
        return None
    address = item.get("address") or {}
    return {
        "name": address.get("city") or address.get("town") or address.get("village") or item.get("name") or name,
        "place_name": item.get("name") or name,
        "display_name": item.get("display_name"),
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "region": address.get("state"),
        "country": address.get("country"),
        "importance": item.get("importance"),
        "type": item.get("addresstype") or item.get("type"),
    }


def _viewbox(near: tuple[float, float], km: float) -> str:
    dlat = km / 111.0
    dlon = km / max(1.0, 111.0 * abs(math.cos(math.radians(near[0]))))
    return f"{near[1] - dlon:.4f},{near[0] + dlat:.4f},{near[1] + dlon:.4f},{near[0] - dlat:.4f}"


def _nominatim_query(name: str, near: tuple[float, float] | None, bounded: bool) -> dict[str, Any] | None:
    rules = load_rules("geo")["nominatim"]
    key = normalize(name) + (f"|{near[0]:.2f},{near[1]:.2f}" if near and bounded else "")
    cached = cache_get("geocode", key)
    if cached is not None:
        return cached["data"]
    params: dict[str, Any] = {"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": rules["language"]}
    if near and bounded:
        params.update(viewbox=_viewbox(near, float(rules["local_bias_km"])), bounded=1)
    payload = get_json("nominatim", f"{NOMINATIM_URL}/search", params=params, timeout=6.0)
    result = _nominatim_result(payload[0], name) if isinstance(payload, list) and payload else None
    cache_set("geocode", key, result, CACHE_TTL_GEOCODE_S, source="nominatim")
    return result


def _nominatim_search(name: str, near: tuple[float, float] | None = None) -> dict[str, Any] | None:
    """Look a name up near the traveller first; accept a distant match only if it is a real destination-level place."""
    rules = load_rules("geo")["nominatim"]
    if near:
        local = _nominatim_query(name, near, bounded=True)
        if local:
            return local
    if is_generic_name(name):
        return None  # "city centre" exists in every city: never resolve it worldwide
    found = _nominatim_query(name, None, bounded=False)
    if not found or not near:
        return found
    if haversine_km(near[0], near[1], found["lat"], found["lon"]) <= float(rules["local_bias_km"]):
        return found
    settlement = (found.get("type") or "") in set(rules["far_accept_types"])
    famous = float(found.get("importance") or 0) >= float(rules["far_min_importance"])
    return found if settlement or famous else None


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
        found = _nominatim_search(name, near)
    except ProviderError as exc:
        errors.append(exc.as_dict())
        return None, errors
    if not found:
        return None, errors
    destination = nearest_destination(found["lat"], found["lon"])
    settlement = (found.get("type") or "") in set(load_rules("geo")["nominatim"]["far_accept_types"])
    return ResolvedPlace(
        name=found["name"] if settlement else found.get("place_name") or found["name"],
        lat=found["lat"],
        lon=found["lon"],
        kind="geocoded" if settlement else "poi",
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
    """The stored city the point lies in (within its city extent), if any; the closest relative to size wins."""
    with SessionLocal() as db:
        destinations = db.scalars(select(Destination)).all()
    containing = []
    for destination in destinations:
        extent = city_extent_km(destination)
        distance = haversine_km(lat, lon, destination.lat, destination.lon)
        if extent > 0 and distance <= extent:
            containing.append((distance / extent, destination))
    return min(containing, key=lambda item: item[0])[1] if containing else None


def reverse_geocode(lat: float, lon: float) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Name the area around a coordinate: curated destination first, then Nominatim."""
    destination = nearest_destination(lat, lon)
    if destination:
        return {"name": destination.name, "region": destination.region, "country": destination.country, "destination_id": destination.id, "source": "database"}, []
    key = f"en|{lat:.3f},{lon:.3f}"
    cached = cache_get("reverse_geocode", key)
    if cached is not None:
        return cached["data"], []
    try:
        payload = get_json("nominatim", f"{NOMINATIM_URL}/reverse", params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14, "addressdetails": 1, "accept-language": load_rules("geo")["nominatim"]["language"]}, timeout=5.0)
    except ProviderError as exc:
        return None, [exc.as_dict()]
    address = (payload or {}).get("address") or {}
    name = address.get("suburb") or address.get("city") or address.get("town") or address.get("village") or address.get("county")
    city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality") or address.get("state_district") or address.get("county")
    result = {"name": name, "city": city, "region": address.get("state"), "country": address.get("country"), "destination_id": None, "source": "nominatim"} if name else None
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
