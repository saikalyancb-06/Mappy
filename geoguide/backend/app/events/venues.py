"""Link event venues to places GeoGuide already knows (no duplicate place records are created)."""
from __future__ import annotations

from sqlalchemy import select

from app.core.text import name_similarity, normalize
from app.db.models import Poi
from app.db.session import SessionLocal
from app.events.model import NormalisedEvent
from app.geo.city import City
from app.geo.distance import haversine_km


def enrich_venues(events: list[NormalisedEvent], city: City) -> int:
    named = [e for e in events if e.venue_name and not e.venue_place_id]
    if not named or not city.destination_id:
        return 0
    with SessionLocal() as db:
        places = db.execute(select(Poi.id, Poi.name, Poi.lat, Poi.lon, Poi.category).where(Poi.destination_id == city.destination_id)).all()
    linked = 0
    for event in named:
        venue = normalize(event.venue_name)
        best, best_score = None, 0.0
        for place in places:
            score = name_similarity(event.venue_name, place.name)
            if venue and (venue == normalize(place.name) or (len(venue) > 5 and venue in normalize(place.name))):
                score = max(score, 0.95)
            if event.lat is not None and place.lat is not None and haversine_km(event.lat, event.lon, place.lat, place.lon) > 2:
                continue  # same name, different place
            if score > best_score:
                best, best_score = place, score
        if best is not None and best_score >= 0.85:
            event.venue_place_id = best.id
            if event.lat is None and best.lat is not None:
                event.lat, event.lon = best.lat, best.lon
            linked += 1
    return linked
