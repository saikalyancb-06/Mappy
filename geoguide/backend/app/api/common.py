"""Shared request parsing for the thin API layer."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.geo.geo_context import GeoContext, build_geo_context


def location_from_params(lat: float | None, lon: float | None, accuracy_m: float | None, timestamp: str | None, source: str | None = None) -> dict[str, Any] | None:
    if lat is None or lon is None:
        return None
    return {"lat": lat, "lon": lon, "accuracy_m": accuracy_m, "timestamp": timestamp, "source": source or "device"}


def destination_from_params(destination_id: str | None, destination: str | None) -> dict[str, Any] | None:
    if destination_id:
        return {"id": destination_id}
    if destination:
        return {"name": destination}
    return None


def require_reference(geo: GeoContext) -> GeoContext:
    if geo.reference is None:
        code = geo.reference_required or "location_or_destination"
        message = {
            "user_location": "Your current location is needed for this. Turn on location access or choose a destination.",
            "resolvable_place": "That place could not be found.",
        }.get(code, "A current location or a destination is required.")
        raise HTTPException(status_code=422, detail={"code": f"{code}_required", "message": message, "warnings": geo.warnings})
    return geo


def geo_for(origin: str, user_location: dict[str, Any] | None, destination: dict[str, Any] | None, radius_km: float | None = None) -> GeoContext:
    """origin: 'user' (near me), 'destination' (active destination) or 'auto'."""
    if origin == "user":
        return require_reference(build_geo_context(user_location=user_location, active_destination=destination, spatial_relation="near_me", radius_km=radius_km))
    if origin == "destination" and not destination:
        raise HTTPException(status_code=422, detail={"code": "destination_required", "message": "Choose a destination first."})
    return require_reference(build_geo_context(user_location=user_location, active_destination=destination, radius_km=radius_km))
