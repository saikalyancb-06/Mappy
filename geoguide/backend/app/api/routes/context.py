"""City-and-date context: the Explore screen and "what's happening" lists."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.common import destination_from_params, location_from_params
from app.context.engine import build_city_context
from app.core.dates import parse_range, single
from app.events.engine import default_providers
from app.events.intent import event_intent
from app.events.service import city_events, events_overview
from app.geo.city import City, city_from_geo
from app.geo.geo_context import GeoContext, build_geo_context
from app.services.profile import resolve_profile
from app.weather.open_meteo import local_now

router = APIRouter(prefix="/api")


def _city(lat, lon, accuracy_m, timestamp, destination_id, destination) -> tuple[City, GeoContext, list]:
    geo = build_geo_context(user_location=location_from_params(lat, lon, accuracy_m, timestamp), active_destination=destination_from_params(destination_id, destination))
    city, errors = city_from_geo(geo)
    if city is None:
        raise HTTPException(status_code=422, detail={"code": "city_required", "message": "Choose a city or turn on your location so GeoGuide knows which city to describe.", "warnings": geo.warnings})
    return city, geo, errors + geo.provider_errors


def _date(value: str | None, city: City) -> date:
    if not value:
        return local_now(city.timezone).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail={"code": "invalid_date", "message": "Use a date like 2026-10-22."}) from None


@router.get("/context")
def city_context(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None,
    date: str | None = Query(None, alias="date", max_length=10), language: str = "en",
    authorization: str | None = Header(default=None),
) -> dict:
    city, geo, errors = _city(lat, lon, accuracy_m, timestamp, destination_id, destination)
    selected = _date(date, city)
    result = build_city_context(city, selected, profile=resolve_profile(authorization), user_point=geo.user_point(), language=language if language in {"en", "kn", "hi"} else "en")
    result["provider_errors"] = errors + result["provider_errors"]
    result["geo_context"] = geo.as_dict()
    return result


@router.get("/events")
def events(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None,
    date: str | None = Query(None, max_length=10), end: str | None = Query(None, max_length=10),
    when: str | None = Query(None, max_length=80, description="e.g. 'tonight', 'this weekend', 'next week', 'Oct 22', 'in October', 'during my trip'"),
    q: str | None = Query(None, max_length=120, description="keywords, e.g. 'jazz', 'food festival'"),
    category: str | None = Query(None, max_length=200, description="comma-separated event categories"),
    free: bool = False, festival: bool = False,
    near: str | None = Query(None, pattern="^(me|destination)$"), radius_km: float | None = Query(None, gt=0, le=100),
    trip_start: str | None = Query(None, max_length=10), trip_end: str | None = Query(None, max_length=10),
    limit: int = Query(40, ge=1, le=100),
    authorization: str | None = Header(default=None),
) -> dict:
    """Verified events for the destination (default) or near the traveller (near=me), for a date or range."""
    city, geo, errors = _city(lat, lon, accuracy_m, timestamp, destination_id, destination)
    anchor = _date(date, city)
    window = single(anchor)
    if near == "me":
        if geo.user_location is None:
            raise HTTPException(status_code=422, detail={"code": "user_location_required", "message": "Turn on location to see events near you."})
        from app.geo.city import resolve_city

        city = resolve_city(lat=geo.user_location.lat, lon=geo.user_location.lon)[0] or city
    trip = (_date(trip_start, city), _date(trip_end, city)) if trip_start and trip_end else None
    if end:
        last = _date(end, city)
        if last < anchor or (last - anchor).days > 62:
            raise HTTPException(status_code=422, detail={"code": "invalid_range", "message": "The end date must be on or after the start date, within about two months."})
        from app.core.dates import DateRange, fmt_range

        window = DateRange(anchor, last, fmt_range(anchor, last), "range")
    elif when:
        window = parse_range(when, anchor, trip) or window
    wants = event_intent(" ".join(filter(None, [when, q])))
    categories = [c.strip() for c in (category or "").split(",") if c.strip()] or wants.categories
    result = city_events(city, window, user_point=geo.user_point(), profile=resolve_profile(authorization), text=q, categories=categories,
                         free_only=free or wants.free_only, festival_only=festival or wants.festival_only,
                         mode="near_me" if near == "me" else "destination", radius_km=radius_km, limit=limit)
    result["provider_errors"] = errors + [s["error"] for s in result["sources_checked"] if s.get("error") and s.get("status") == "error"]
    return result


@router.get("/events/overview")
def events_overview_route(
    lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None, timestamp: str | None = None,
    destination_id: str | None = None, destination: str | None = None, date: str | None = Query(None, max_length=10),
    near: str | None = Query(None, pattern="^(me|destination)$"), radius_km: float | None = Query(None, gt=0, le=100),
    authorization: str | None = Header(default=None),
) -> dict:
    """The next weeks of verified events, split into the tabs that have something (Today, Tonight, This weekend, Festivals, Free…)."""
    city, geo, errors = _city(lat, lon, accuracy_m, timestamp, destination_id, destination)
    if near == "me" and geo.user_location is None:
        raise HTTPException(status_code=422, detail={"code": "user_location_required", "message": "Turn on location to see events near you."})
    result = events_overview(city, _date(date, city), user_point=geo.user_point(), profile=resolve_profile(authorization), mode="near_me" if near == "me" else "destination", radius_km=radius_km)
    result["provider_errors"] = errors + [s["error"] for s in result["sources_checked"] if s.get("error") and s.get("status") == "error"]
    return result


@router.get("/events/providers")
def event_providers() -> dict:
    """Which event providers are configured on this server (no keys are ever returned)."""
    return {"providers": [provider.health_check() for provider in default_providers()]}
