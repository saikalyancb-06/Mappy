from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from app.config import APP_ENV
from app.api.common import destination_from_params, geo_for, location_from_params
from app.core.logging import Trace
from app.core.rules import taxonomy
from app.geo import opening_hours
from app.geo.geo_context import build_geo_context, destination_by_id, distance_from_user
from app.geo.spatial import get_pois
from app.knowledge.context import active_advisories
from app.retrieval.knowledge import retrieve
from app.query.constraints import Constraints, parse_constraints
from app.ranking.ranker import RankRequest, rank
from app.services.cost import to_decimal
from app.services.discovery import DiscoveryRequest, discover
from app.services.hotels import HotelRequest, find_hotels
from app.services.search import search
from app.services.now_service import build_now
from app.services.profile import resolve_profile
from app.weather.open_meteo import get_weather, local_now

router = APIRouter(prefix="/api")


@router.get("/now")
def now(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None, language: str = "en",
    authorization: str | None = Header(default=None),
) -> dict:
    geo = geo_for("auto", location_from_params(lat, lon, accuracy_m, timestamp), destination_from_params(destination_id, destination))
    profile = resolve_profile(authorization)
    return build_now(geo, profile, language=language if language in {"en", "kn", "hi"} else "en")


@router.get("/nearby")
def nearby(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None,
    origin: str = Query("auto", pattern="^(auto|user|destination)$"),
    category: str | None = None, group: str | None = None, kind: str | None = None,
    radius_km: float | None = Query(None, gt=0, le=50), open_now: bool = False, limit: int = Query(20, ge=1, le=50), debug: bool = False,
    text: str | None = Query(None, max_length=300), ranking_mode: str | None = Query(None, pattern="^(popular|local|hidden_gems)$"),
    travel_mode: str | None = Query(None, pattern="^(walk|bicycle|motorbike|car|auto|transit)$"), within_budget: bool = False,
    authorization: str | None = Header(default=None),
) -> dict:
    if category and category not in taxonomy()["categories"]:
        raise HTTPException(status_code=422, detail=f"Unknown category '{category}'.")
    if group and group not in taxonomy()["groups"]:
        raise HTTPException(status_code=422, detail=f"Unknown group '{group}'.")
    geo = geo_for(origin, location_from_params(lat, lon, accuracy_m, timestamp), destination_from_params(destination_id, destination), radius_km)
    reference = geo.reference
    trace = Trace("nearby")
    destination_row = destination_by_id(reference.destination_id)
    weather = get_weather(reference.lat, reference.lon, 0)
    tz = (destination_row.timezone if destination_row else None) or (weather.get("timezone") if weather.get("status") == "ok" else None)
    kinds = {kind} if kind else (None if (category or group) else set(taxonomy()["attraction_kinds"]))
    constraints = parse_constraints(text) if text else Constraints()
    constraints.ranking_mode = ranking_mode or constraints.ranking_mode
    constraints.travel_mode = travel_mode or constraints.travel_mode
    if constraints.include_categories or constraints.include_groups:
        kinds = None
    result = discover(DiscoveryRequest(
        reference=reference,
        profile_name="nearby" if reference.semantic == "near_me" else "discovery",
        category=category, group=group, kinds=kinds,
        user=resolve_profile(authorization),
        local_time=local_now(tz), time_sensitive=True, require_open=open_now,
        weather_signals=weather.get("signals") or [],
        limit=limit, user_point=geo.user_point(),
        constraints=constraints, within_budget=within_budget,
    ), trace=trace)
    trace.emit()
    items = []
    for candidate in result.candidates:
        data = candidate.as_dict()
        data["distance_from_user_km"] = distance_from_user(geo, candidate.lat, candidate.lon)
        items.append(data)
    return {
        "request_id": trace.request_id,
        "items": items,
        "geo_context": geo.as_dict(),
        "counts": result.counts,
        "filtered_out": len(result.filtered_out),
        "provider_errors": result.provider_errors,
        "weather_signals": weather.get("signals") or [],
        "understood": constraints.understood if text else [],
        "data_status": "partial" if result.provider_errors else "live",
        **({"debug": trace.as_dict()} if debug and APP_ENV != "production" else {}),
    }


@router.get("/places/{poi_id}")
def place_detail(poi_id: str, lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None, authorization: str | None = Header(default=None)) -> dict:
    geo = build_geo_context(user_location=location_from_params(lat, lon, accuracy_m, timestamp), active_destination=None)
    found = get_pois([poi_id], geo.user_point())
    if not found:
        raise HTTPException(status_code=404, detail="Place not found in stored data.")
    candidate = found[0]
    destination = destination_by_id(candidate.destination_id)
    now_local = local_now(destination.timezone if destination else None)
    if candidate.opening_hours:
        status = opening_hours.status_at(candidate.opening_hours, now_local)
        candidate.open_status = status["status"]
        candidate.open_detail = {k: v for k, v in status.items() if k != "status"}
    facts = []
    if candidate.destination_id:
        facts = [hit.as_dict() for hit in retrieve(candidate.name, destination_id=candidate.destination_id, poi_ids=[candidate.id], limit=8).hits if hit.poi_id == candidate.id and hit.category != "description"]
    advisories = [a for a in active_advisories(candidate.destination_id, now_local.date()) if a.get("poi_id") in (None, candidate.id)] if candidate.destination_id else []
    point = geo.user_point() or ((destination.lat, destination.lon) if destination else None)
    ranked = rank([candidate], RankRequest(profile_name="lookup", reference=point, reference_label="you" if geo.user_point() else (f"{destination.name} centre" if destination else None), user=resolve_profile(authorization), local_time=now_local, time_sensitive=True, user_point=geo.user_point())).ranked
    candidate = ranked[0] if ranked else candidate
    data = candidate.as_dict()
    data["distance_from_user_km"] = distance_from_user(geo, candidate.lat, candidate.lon)
    return {"place": data, "facts": facts, "advisories": advisories, "destination": {"id": destination.id, "name": destination.name} if destination else None, "local_time": now_local.isoformat(timespec="minutes"), "location_status": geo.location_status}


@router.get("/hotels")
def hotels(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None,
    origin: str = Query("auto", pattern="^(auto|user|destination)$"),
    sort: str = Query("best", pattern="^(best|cheapest|nearest|top_rated)$"),
    check_in: str | None = None, nights: int = Query(1, ge=1, le=30), max_price: str | None = None, min_stars: int | None = Query(None, ge=1, le=5),
    within_budget: bool = False, radius_km: float | None = Query(None, gt=0, le=50), limit: int = Query(20, ge=1, le=50),
    authorization: str | None = Header(default=None),
) -> dict:
    """Best stays around the traveller or in the destination, with live nightly rates when available."""
    geo = geo_for(origin, location_from_params(lat, lon, accuracy_m, timestamp), destination_from_params(destination_id, destination), radius_km or (5.0 if origin == "user" else None))
    try:
        check_in_date = date.fromisoformat(check_in) if check_in else None
    except ValueError:
        raise HTTPException(status_code=422, detail="check_in must be YYYY-MM-DD.") from None
    price_cap = to_decimal(max_price)
    if max_price and price_cap is None:
        raise HTTPException(status_code=422, detail="max_price must be an amount, e.g. 3000.00")
    trace = Trace("hotels")
    result = find_hotels(HotelRequest(reference=geo.reference, profile=resolve_profile(authorization), sort=sort, check_in=check_in_date, nights=nights, max_price=price_cap, min_stars=min_stars, within_budget=within_budget, user_point=geo.user_point(), limit=limit), trace=trace)
    trace.emit()
    items = []
    for candidate in result["items"]:
        data = candidate.as_dict()
        data["distance_from_user_km"] = distance_from_user(geo, candidate.lat, candidate.lon)
        items.append(data)
    return {**result, "items": items, "geo_context": geo.as_dict(), "request_id": trace.request_id}


@router.get("/search")
def search_places(
    q: str = Query(..., min_length=2, max_length=120),
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None,
    kind: str | None = Query(None, pattern="^(place|stay|destination)$"), limit: int = Query(12, ge=1, le=30),
    authorization: str | None = Header(default=None),
) -> dict:
    """Search a place, hotel, destination or category by name — stored data first, live search when needed."""
    geo = build_geo_context(user_location=location_from_params(lat, lon, accuracy_m, timestamp), active_destination=destination_from_params(destination_id, destination))
    trace = Trace("search", q)
    result = search(q, reference=geo.reference, user_point=geo.user_point(), profile=resolve_profile(authorization), kind=kind, limit=limit, trace=trace)
    trace.emit()
    items = []
    for candidate in result["places"]:
        data = candidate.as_dict()
        data["distance_from_user_km"] = distance_from_user(geo, candidate.lat, candidate.lon)
        items.append(data)
    return {**result, "places": items, "geo_context": geo.as_dict(), "request_id": trace.request_id}
