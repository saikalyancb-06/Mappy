"""Keep reusable live place results.

A live Google Maps look-up (via SerpApi) made while answering a question is
stored when it describes a real place inside a registered city (place id plus
coordinates inside the boundary), so the next similar question is answered from
the database. Transient or out-of-area results are not stored.
"""
from __future__ import annotations

import logging

from app.core.rules import load_rules
from app.db.models import Destination
from app.db.session import SessionLocal
from app.places.providers.base import PlaceResult
from app.places.store import UpsertReport, upsert_places
from app.search.serpapi import SearchResult

logger = logging.getLogger(__name__)


def place_from_search_result(result: SearchResult) -> PlaceResult | None:
    if result.engine != "google_maps" or not result.place_id:
        return None
    return PlaceResult(
        provider="serpapi_google_maps", external_id=result.place_id, name=result.title, lat=result.latitude, lon=result.longitude,
        address=result.address, types=list(result.place_types), primary_type=result.place_type, rating=result.rating, review_count=result.review_count,
        open_state=result.open_state, phone=result.phone, website=result.website, description=result.snippet or None,
        images=[result.image_url] if result.image_url else [], permanently_closed="permanently closed" in (result.open_state or "").lower(),
    )


def persist_live_results(destination_id: str | None, results: list[SearchResult]) -> UpsertReport | None:
    if not destination_id or not results or not load_rules("city_intelligence")["places"]["persist_live_results"]:
        return None
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
    if destination is None:
        return None
    places = [p for p in (place_from_search_result(r) for r in results) if p]
    if not places:
        return None
    report = upsert_places(destination, places, component="live", keep_raw=False)
    logger.info("live_results_persisted destination=%s %s", destination_id, report.as_dict())
    return report
