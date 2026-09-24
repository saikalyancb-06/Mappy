"""PlaceResult (any provider) → GeoGuide POI fields. Provider structures stop here."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.rules import load_rules, taxonomy
from app.core.text import normalize
from app.geo import opening_hours
from app.places.providers.base import PlaceResult
from app.search.normalizer import classify_category

COMPONENT_CATEGORY_HINTS = {"museums": "museum", "parks": "park", "viewpoints": "viewpoint", "nature": "park", "markets": "market", "food": "restaurant", "landmarks": "monument", "activities": "activity", "family": "activity"}


def price_level(text: str | None) -> int | None:
    """"$$" / "₹₹₹" style price levels only; ranges like "₹200–400" are kept as text, not guessed into a level."""
    if not text:
        return None
    symbols = re.fullmatch(r"\s*([$€£₹¥])\1{0,3}\s*", text)
    return len(text.strip()) if symbols else None


def maps_url(place: PlaceResult) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place.external_id}" if not place.external_id.startswith("0x") else f"https://www.google.com/maps/search/?api=1&query={place.lat},{place.lon}"


def poi_values(place: PlaceResult, *, component: str | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    """Normalised POI columns for a provider place; None when it cannot be a place (no coordinates)."""
    if place.lat is None or place.lon is None:
        return None
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    category = classify_category([place.primary_type or "", *place.types, place.name], fallback=COMPONENT_CATEGORY_HINTS.get(component or ""))
    kind = taxonomy()["categories"].get(category or "", {}).get("kind") or "attraction"
    hours = opening_hours.parse_google(place.hours)
    ttl = load_rules("city_intelligence")["places"]["place_ttl_days"]
    return {
        "name": place.name,
        "normalized_name": normalize(place.name),
        "kind": kind,
        "category": category,
        "subcategory": place.primary_type,
        "provider_types": json.dumps(place.types[:12]),
        "lat": float(place.lat),
        "lon": float(place.lon),
        "coordinate_precision": "exact",
        "address": place.address,
        "description": place.description,
        "opening_hours": json.dumps(hours) if hours else None,
        "opening_hours_raw": json.dumps(place.hours) if place.hours else None,
        "price_level": price_level(place.price_text),
        "rating": place.rating,
        "review_count": place.review_count,
        "phone": place.phone,
        "website": place.website,
        "images": json.dumps(place.images) if place.images else None,
        "status": "permanently_closed" if place.permanently_closed else "active",
        "source": "Google Maps (via SerpApi)",
        "source_url": maps_url(place),
        "source_id": place.external_id,
        "external_place_id": place.external_id,
        "provider_data_id": place.data_id,
        "data_source_id": "google_maps",
        "confidence": 0.8,
        "metadata_json": json.dumps({k: v for k, v in {"price_text": place.price_text, "open_state": place.open_state, "found_by": component}.items() if v}),
        "fetched_at": now,
        "updated_at": now,
        "last_verified_at": now,
        "expires_at": now + timedelta(days=ttl),
    }
