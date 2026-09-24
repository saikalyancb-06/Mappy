"""City-and-date context: the Explore screen and "what's happening" lists."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.common import destination_from_params, location_from_params
from app.context.engine import build_city_context
from app.core.dates import parse_range, single
from app.events.service import city_events
from app.geo.city import City, city_from_geo
from app.geo.geo_context import build_geo_context
from app.services.profile import resolve_profile
from app.weather.open_meteo import local_now

router = APIRouter(prefix="/api")


def _city(lat, lon, accuracy_m, timestamp, destination_id, destination) -> tuple[City, object, list]:
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
    when: str | None = Query(None, max_length=80, description="e.g. 'this weekend', 'next week', 'Oct 22', 'in October'"),
) -> dict:
    city, geo, errors = _city(lat, lon, accuracy_m, timestamp, destination_id, destination)
    anchor = _date(date, city)
    window = single(anchor)
    if end:
        last = _date(end, city)
        if last < anchor or (last - anchor).days > 62:
            raise HTTPException(status_code=422, detail={"code": "invalid_range", "message": "The end date must be on or after the start date, within about two months."})
        from app.core.dates import DateRange, fmt_range

        window = DateRange(anchor, last, fmt_range(anchor, last), "range")
    elif when:
        window = parse_range(when, anchor) or window
    result = city_events(city, window, today=local_now(city.timezone).date(), user_point=geo.user_point())
    result["provider_errors"] = errors + [s["error"] for s in result["sources_checked"] if s.get("error")]
    return result
