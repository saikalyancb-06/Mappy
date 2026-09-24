"""Route-aware suggestions: interesting places *on the way* from A to B.

Candidates come from a corridor around the straight line between the two
points; each gets a detour estimate (extra distance/time versus going
direct) and places whose detour exceeds the limit are dropped. Travel times
are estimates from straight-line distance, labelled as such.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import Trace
from app.core.rules import load_rules, taxonomy
from app.geo.distance import haversine_km
from app.geo.geo_context import ActiveReference
from app.models import Candidate
from app.services.discovery import DiscoveryRequest, discover

ENDPOINT_KM = 0.25
DEFAULT_MAX_DETOUR_MIN = 15


@dataclass
class RouteRequest:
    origin: tuple[float, float]
    origin_label: str
    destination: tuple[float, float]
    destination_label: str
    travel_mode: str | None = None
    max_detour_min: int | None = None
    user: dict[str, Any] = field(default_factory=dict)
    constraints: Any = None
    category: str | None = None
    limit: int = 8


def route_suggestions(request: RouteRequest, trace: Trace | None = None) -> dict[str, Any]:
    travel = load_rules("ranking")["travel"]
    mode = request.travel_mode or (request.constraints.travel_mode if request.constraints else None) or (request.user or {}).get("travel_mode") or "car"
    speed = travel["speed_kmh"].get(mode, 22)
    factor = travel["detour_factor"]
    direct_km = haversine_km(*request.origin, *request.destination)
    direct_min = int(round(direct_km * factor / speed * 60))
    max_detour = request.max_detour_min or DEFAULT_MAX_DETOUR_MIN
    buffer_km = speed * max_detour / 60 / factor / 2
    mid = ((request.origin[0] + request.destination[0]) / 2, (request.origin[1] + request.destination[1]) / 2)
    corridor = ActiveReference(origin="route", lat=mid[0], lon=mid[1], label=f"the route to {request.destination_label}", radius_km=round(direct_km / 2 + buffer_km, 2), semantic="near_place")
    result = discover(DiscoveryRequest(
        reference=corridor,
        profile_name="discovery",
        category=request.category,
        kinds=None if request.category else set(taxonomy()["attraction_kinds"]) | {"food"},
        user=request.user,
        constraints=request.constraints,
        limit=60,
        travel_origin=request.origin,
    ), trace=trace)
    on_way: list[Candidate] = []
    for candidate in result.candidates:
        extra_km = haversine_km(*request.origin, candidate.lat, candidate.lon) + haversine_km(candidate.lat, candidate.lon, *request.destination) - direct_km
        # The start and end themselves are not suggestions.
        if min(haversine_km(*request.origin, candidate.lat, candidate.lon), haversine_km(candidate.lat, candidate.lon, *request.destination)) < ENDPOINT_KM:
            continue
        detour = int(round(max(0.0, extra_km) * factor / speed * 60))
        if detour > max_detour:
            continue
        candidate.detour_min = detour
        candidate.travel_mode = mode
        to_place = haversine_km(*request.origin, candidate.lat, candidate.lon)
        candidate.travel_min = int(round(to_place * factor / speed * 60))
        candidate.distance_km = round(to_place, 3)
        candidate.reasons = [f"Adds ~{detour} min to your route by {mode} (estimate)", *[r for r in candidate.reasons if " from " not in r]]
        candidate.score = round((candidate.score or 0) - detour / max(max_detour, 1) * 0.15, 4)
        on_way.append(candidate)
    on_way.sort(key=lambda c: -(c.score or 0))
    limit = request.constraints.limit if request.constraints is not None and request.constraints.limit else request.limit
    if trace:
        trace.step("route_corridor", origin=request.origin_label, destination=request.destination_label, direct_km=round(direct_km, 2), direct_min=direct_min, mode=mode, max_detour_min=max_detour, corridor_radius_km=corridor.radius_km, candidates=len(result.candidates), on_way=len(on_way))
    return {
        "items": on_way[:limit],
        "direct_km": round(direct_km, 2),
        "direct_min": direct_min,
        "mode": mode,
        "max_detour_min": max_detour,
        "provider_errors": result.provider_errors,
    }
