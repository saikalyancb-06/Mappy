"""Stays: the best hotels around a reference point, with live nightly rates when available.

Stored hotels (curated packs, organiser dataset, OSM) are merged with Google
Hotels results; rates are only ever shown when a live source published them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.core.logging import Trace
from app.geo.geo_context import ActiveReference, destination_by_id
from app.geo.spatial import SpatialQuery, nearby
from app.models import Candidate
from app.ranking.ranker import RankRequest, rank
from app.search.aggregator import aggregate
from app.search.normalizer import candidate_from_hotel
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client
from app.services.cost import to_decimal
from app.weather.open_meteo import local_now

SORTS = {"best", "cheapest", "nearest", "top_rated"}


@dataclass
class HotelRequest:
    reference: ActiveReference
    profile: dict[str, Any] = field(default_factory=dict)
    sort: str = "best"
    check_in: date | None = None
    nights: int = 1
    max_price: Decimal | None = None
    min_stars: int | None = None
    within_budget: bool = False
    user_point: tuple[float, float] | None = None
    limit: int = 20


def _currency(request: HotelRequest) -> str:
    destination = destination_by_id(request.reference.destination_id)
    return (request.profile.get("budget_currency") or (destination.currency if destination else None) or "INR").upper()


def find_hotels(request: HotelRequest, trace: Trace | None = None, web: SerpApiClient | None = None) -> dict[str, Any]:
    web = web or default_client
    reference = request.reference
    destination = destination_by_id(reference.destination_id)
    check_in = request.check_in or local_now(destination.timezone if destination else None).date()
    check_out = check_in + timedelta(days=max(1, request.nights))
    stored = nearby(SpatialQuery(lat=reference.lat, lon=reference.lon, radius_km=reference.radius_km, kinds=["stay"]))
    live: list[Candidate] = []
    errors: list[dict[str, str]] = []
    currency = _currency(request)
    if web.configured:
        place = destination.name if destination else None
        query = f"hotels in {place}" if place and reference.semantic != "near_me" else f"hotels near {reference.lat:.4f},{reference.lon:.4f}"
        try:
            response = web.search(query, engine="google_hotels", limit=20, extra={"check_in_date": check_in.isoformat(), "check_out_date": check_out.isoformat(), "currency": currency, "gl": "in"})
            live = [c for c in (candidate_from_hotel(r, response.retrieved_at, reference=(reference.lat, reference.lon)) for r in response.results) if c]
            if trace:
                trace.step("hotels_live", query=query, results=len(response.results), cached=response.cached)
        except SearchProviderError as exc:
            errors.append(exc.as_dict())
    else:
        errors.append({"source": "serpapi", "code": "web_search_unavailable", "message": "Live hotel prices need web search (SERPAPI_KEY)."})

    merged, duplicates = aggregate([stored, live])
    profile = dict(request.profile)
    result = rank(merged, RankRequest(profile_name="stays", reference=(reference.lat, reference.lon), reference_label=reference.distance_label, radius_km=reference.radius_km, kinds={"stay"}, user=profile, within_budget=request.within_budget, user_point=request.user_point))
    hotels = result.ranked
    if request.min_stars:
        hotels = [h for h in hotels if (h.star_rating or 0) >= request.min_stars]
    if request.max_price is not None:
        hotels = [h for h in hotels if to_decimal(h.price_per_night) is None or to_decimal(h.price_per_night) <= request.max_price]
    if request.sort == "cheapest":
        hotels.sort(key=lambda h: (to_decimal(h.price_per_night) is None, to_decimal(h.price_per_night) or Decimal(0), -(h.score or 0)))
    elif request.sort == "nearest":
        hotels.sort(key=lambda h: h.distance_km if h.distance_km is not None else 1e9)
    elif request.sort == "top_rated":
        hotels.sort(key=lambda h: (-(h.guest_score / 2 if h.guest_score is not None else h.rating or 0), -(h.review_count or 0)))
    if trace:
        trace.step("hotels", stored=len(stored), live=len(live), duplicates_merged=duplicates, sort=request.sort, returned=min(len(hotels), request.limit))
    return {
        "items": hotels[: request.limit],
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "currency": currency,
        "counts": {"stored": len(stored), "live": len(live), "duplicates_merged": duplicates},
        "prices_available": any(h.price_per_night for h in hotels),
        "provider_errors": errors,
    }
