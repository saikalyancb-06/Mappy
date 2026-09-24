"""Offline city pack: everything needed to explore a city with no connection.

A compact, versioned bundle the app stores on the device (IndexedDB):
places (with structured opening hours, so "open now" still works offline),
city knowledge, safety advisories, stored/official events for the coming weeks,
and the category taxonomy. Nothing live (weather, live listings) is included:
offline answers say they come from saved data and when it was saved.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.cities.registry import city_dict
from app.core.dates import DateRange
from app.core.rules import taxonomy
from app.db.models import Destination, KnowledgeChunk, Poi
from app.db.session import SessionLocal
from app.events.providers.calendar import CalendarProvider
from app.events.providers.stored import StoredEventProvider
from app.events.service import city_events
from app.geo import opening_hours
from app.geo.city import city_for_destination
from app.knowledge.context import active_advisories
from app.models import candidate_from_poi
from app.policy.recommendation import context_from_profile, exclusion_reason

PACK_FORMAT = 1
EVENT_DAYS = 45
MAX_PLACES = 1500
DESCRIPTION_CHARS = 360

_PLACE_FIELDS = ("id", "name", "category", "kind", "lat", "lon", "address", "neighborhood", "rating", "review_count", "price_level", "entry_cost",
                 "fee_currency", "phone", "website", "image_url", "step_free", "indoor", "visit_duration_min", "tags", "provider_types")


def _place(poi: Poi) -> dict[str, Any]:
    candidate = candidate_from_poi(poi)
    data = {key: getattr(candidate, key, None) for key in _PLACE_FIELDS}
    data["opening_hours"] = opening_hours.load(poi.opening_hours) or opening_hours.parse_osm(poi.opening_hours_raw)
    data["hours_text"] = candidate.opening_hours_text
    data["description"] = (poi.description or "")[:DESCRIPTION_CHARS] or None
    data["source"] = poi.source
    return {k: v for k, v in data.items() if v not in (None, "", [])}


def build_offline_pack(destination_id: str, profile: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
        if destination is None:
            return None
        pois = db.scalars(select(Poi).where(Poi.destination_id == destination_id).order_by(Poi.review_count.desc().nullslast(), Poi.name).limit(MAX_PLACES * 2)).all()
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.destination_id == destination_id, KnowledgeChunk.kind.in_(["city_kb", "place_kb"]))).all()
    policy = context_from_profile(profile)
    places = []
    for poi in pois:
        if (poi.status or "") == "permanently_closed" or exclusion_reason(candidate_from_poi(poi), policy):
            continue  # the same recommendation policy as online
        places.append(_place(poi))
        if len(places) >= MAX_PLACES:
            break
    today = datetime.now(timezone.utc).date()
    city = city_for_destination(destination_id)
    events: list[dict[str, Any]] = []
    if city is not None:
        found = city_events(city, DateRange(today, today + timedelta(days=EVENT_DAYS), "next few weeks", "range"), today=today, live=False,
                            providers=[StoredEventProvider(), CalendarProvider()], limit=120)
        events = found.get("events") or []
    tax = taxonomy()
    pack = {
        "format": PACK_FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "city": {**city_dict(destination), "summary": destination.summary, "currency": destination.currency},
        "places": places,
        "knowledge": [{"id": c.id, "type": c.category, "title": c.title, "content": c.content, "source": c.source, "source_url": c.source_url} for c in chunks],
        "advisories": active_advisories(destination_id, today),
        "events": events,
        "events_until": (today + timedelta(days=EVENT_DAYS)).isoformat(),
        "taxonomy": {
            "categories": {key: {"label": value["label"], "kind": value["kind"], "synonyms": value.get("synonyms", [])[:12]} for key, value in tax["categories"].items()},
            "groups": {key: {"label": value["label"], "categories": value["categories"]} for key, value in tax["groups"].items()},
        },
    }
    pack["version"] = hashlib.sha1(json.dumps([len(places), destination.data_version, [p["id"] for p in places[:50]], len(chunks), len(events)], default=str).encode()).hexdigest()[:12]
    return pack
