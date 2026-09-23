from __future__ import annotations

from typing import Any


def build_overpass_query(lat: float, lon: float, radius_km: float = 2.0) -> str:
    radius_m = int(radius_km * 1000)
    return f"""
    [out:json][timeout:25];
    (
      node["tourism"](around:{radius_m},{lat},{lon});
      node["historic"](around:{radius_m},{lat},{lon});
      node["natural"](around:{radius_m},{lat},{lon});
      node["amenity"~"museum|market|theatre|place_of_worship|restaurant|cafe"](around:{radius_m},{lat},{lon});
      way["tourism"](around:{radius_m},{lat},{lon});
      way["historic"](around:{radius_m},{lat},{lon});
      relation["tourism"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """.strip()


def normalize_overpass_response(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get('elements', [])
    normalized: list[dict[str, Any]] = []
    for item in items:
        tags = item.get('tags', {})
        name = tags.get('name') or 'Unnamed place'
        lat = item.get('lat')
        lon = item.get('lon')
        if lat is None and 'center' in item:
            lat = item['center'].get('lat')
            lon = item['center'].get('lon')
        normalized.append({
            'id': item.get('id'),
            'name': name,
            'lat': lat,
            'lon': lon,
            'category': tags.get('tourism') or tags.get('historic') or tags.get('amenity') or 'place',
            'tags': tags,
        })
    return normalized
