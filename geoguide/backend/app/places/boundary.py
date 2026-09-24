"""Does a place actually belong to the selected destination?

A text search ranks famous places highly even when they are far outside the
city. A place is kept only if it lies within the destination's boundary (an
explicit ``boundary_radius_km`` or the city's detected extent) and its address
does not put it in a different country.
"""
from __future__ import annotations

from app.core.rules import load_rules
from app.core.text import normalize
from app.db.models import Destination
from app.geo.distance import haversine_km, valid_coordinates
from app.geo.geocoding import city_extent_km


def boundary_km(destination: Destination) -> float:
    return float(destination.boundary_radius_km or city_extent_km(destination) or destination.coverage_radius_km or 10.0)


def _other_country(address: str | None, destination: Destination) -> str | None:
    if not address or not destination.country:
        return None
    tail = " ".join(word for word in normalize(address.split(",")[-1]).split() if not any(ch.isdigit() for ch in word))  # drop postcodes
    own = normalize(destination.country)
    names = load_rules("countries")["names"]
    if tail and tail != own and tail in names and names.get(own) != names[tail]:
        return tail
    return None


def check(destination: Destination, lat: float | None, lon: float | None, address: str | None = None) -> tuple[bool, str]:
    """(inside, reason). Reasons: ok | missing_coordinates | outside_boundary | other_country."""
    if not valid_coordinates(lat, lon):
        return False, "missing_coordinates"
    if haversine_km(destination.lat, destination.lon, float(lat), float(lon)) > boundary_km(destination):
        return False, "outside_boundary"
    if _other_country(address, destination):
        return False, "other_country"
    return True, "ok"
