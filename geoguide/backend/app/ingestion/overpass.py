"""OpenStreetMap (Overpass) live POI retrieval for places without a curated pack."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.config import CACHE_TTL_GEOCODE_S, OVERPASS_URL
from app.core.cache import cache_get, cache_set
from app.core.http import ProviderError, http_client
from app.core.rules import taxonomy
from app.core.text import normalize
from app.db.models import Poi, utcnow
from app.db.session import SessionLocal
from app.geo import opening_hours
from app.geo.distance import valid_coordinates


def build_overpass_query(lat: float, lon: float, radius_km: float = 2.0) -> str:
    radius_m = int(radius_km * 1000)
    around = f"(around:{radius_m},{lat},{lon})"
    return f"""
    [out:json][timeout:20];
    (
      node["tourism"]{around};
      way["tourism"]{around};
      node["historic"]{around};
      way["historic"]{around};
      node["amenity"~"^(cafe|restaurant|fast_food|bar|pub|place_of_worship|marketplace|atm|pharmacy|hospital|clinic|bus_station)$"]{around};
      way["amenity"~"^(place_of_worship|marketplace|hospital)$"]{around};
      node["leisure"~"^(park|garden|nature_reserve)$"]{around};
      way["leisure"~"^(park|garden|nature_reserve)$"]{around};
      node["natural"~"^(peak|water)$"]["name"]{around};
      node["shop"~"^(bakery|mall)$"]{around};
    );
    out center tags 400;
    """.strip()


def classify_osm(tags: dict[str, str]) -> tuple[str | None, str | None]:
    """Map OSM tags to (taxonomy category, kind) using the taxonomy's osm selectors."""
    for category_id, entry in taxonomy()["categories"].items():
        for selector in entry.get("osm", []):
            key, _, value = selector.partition("=")
            if key in tags and (value == "*" or tags[key] == value):
                return category_id, entry.get("kind")
    if "tourism" in tags:
        return "monument", "attraction"
    return None, None


def normalize_overpass_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in payload.get("elements", []):
        tags = item.get("tags") or {}
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue  # unnamed features are not useful recommendations
        lat, lon = item.get("lat"), item.get("lon")
        if lat is None and "center" in item:
            lat, lon = item["center"].get("lat"), item["center"].get("lon")
        if not valid_coordinates(lat, lon):
            continue
        category, kind = classify_osm(tags)
        if not category:
            continue
        address = ", ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb"), tags.get("addr:city")])) or None
        normalized.append({"osm_id": f"{item.get('type', 'node')}/{item.get('id')}", "name": name, "lat": float(lat), "lon": float(lon), "category": category, "kind": kind, "tags": tags, "address": address})
    return normalized


def fetch_and_store(lat: float, lon: float, radius_km: float) -> tuple[int, dict[str, str] | None]:
    """Fetch OSM POIs around a point (cached) and upsert them into ``pois``."""
    key = f"{lat:.3f},{lon:.3f},{radius_km:.1f}"
    if cache_get("overpass", key) is not None:
        return 0, None
    try:
        with http_client(timeout=20.0) as client:
            response = client.post(OVERPASS_URL, data={"data": build_overpass_query(lat, lon, radius_km)})
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return 0, ProviderError("overpass", "unreachable", type(exc).__name__).as_dict()
    places = normalize_overpass_response(payload)
    with SessionLocal() as db:
        existing = {row for row in db.scalars(select(Poi.id).where(Poi.id.in_([f"osm-{p['osm_id'].replace('/', '-')}" for p in places]))).all()}
        for place in places:
            poi_id = f"osm-{place['osm_id'].replace('/', '-')}"
            tags = place["tags"]
            structured = opening_hours.parse_osm(tags.get("opening_hours"))
            values = dict(
                name=place["name"], normalized_name=normalize(place["name"]), kind=place["kind"] or "attraction", category=place["category"],
                tags=json.dumps([]), lat=place["lat"], lon=place["lon"], coordinate_precision="exact", address=place["address"],
                description=tags.get("description"), opening_hours=json.dumps(structured) if structured else None, opening_hours_raw=tags.get("opening_hours"),
                phone=tags.get("phone") or tags.get("contact:phone"), website=tags.get("website") or tags.get("contact:website"),
                step_free=True if tags.get("wheelchair") == "yes" else (False if tags.get("wheelchair") == "no" else None),
                source="OpenStreetMap", source_url=f"https://www.openstreetmap.org/{place['osm_id']}", source_id=place["osm_id"], data_source_id="osm-live", confidence=0.75, fetched_at=utcnow(), updated_at=utcnow(),
            )
            if poi_id in existing:
                row = db.get(Poi, poi_id)
                for field_name, value in values.items():
                    setattr(row, field_name, value)
            else:
                db.add(Poi(id=poi_id, destination_id=None, **values))
        db.commit()
    cache_set("overpass", key, {"count": len(places)}, CACHE_TTL_GEOCODE_S, source="overpass")
    return len(places), None
