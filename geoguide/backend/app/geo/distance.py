from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(a)))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) enclosing a circle; used as an index-friendly prefilter."""
    lat_delta = radius_km / 110.574
    lon_delta = radius_km / max(111.320 * cos(radians(lat)), 1e-6)
    return lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta


def valid_coordinates(lat: object, lon: object) -> bool:
    try:
        la, lo = float(lat), float(lon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0 and not (la == 0.0 and lo == 0.0)
