"""Location resolver: turn a GPS fix or a chosen place into CITY + COUNTRY.

The city is the geographic boundary for city-level context (events, festivals,
place knowledge, weather). POI search keeps using radii; events never do.

Resolution order:
1. an explicit destination id (the traveller chose a city);
2. the stored destination whose coverage area contains the point;
3. reverse geocoding (Nominatim) for the city name, then a stored destination
   with that name in that country;
4. otherwise an ad-hoc city with no stored data. Live sources can still be
   searched by name; nothing is invented for it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.text import normalize
from app.db.models import Destination
from app.geo.distance import haversine_km
from app.geo.geo_context import all_destinations, destination_by_id
from app.geo.geocoding import destination_aliases, nearest_destination, reverse_geocode

# A stored destination with the geocoded name must also be this close to count as the same city.
SAME_CITY_KM = 60.0


@dataclass
class City:
    name: str
    country: str | None
    region: str | None
    lat: float
    lon: float
    timezone: str | None = None
    destination_id: str | None = None  # the stored destination for this city, if any
    resolved_by: str = "destination"  # destination | coverage | geocoder_match | geocoder
    season_profile: str | None = None
    peak_months: list[int] | None = None
    currency: str | None = None
    country_code: str | None = None

    @property
    def key(self) -> str:
        """Stable id for cities with or without a stored destination (used for live events)."""
        return self.destination_id or f"{normalize(self.name)}|{normalize(self.country or '')}"

    @property
    def label(self) -> str:
        return ", ".join(part for part in (self.name, self.country) if part)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "key": self.key, "label": self.label, "has_stored_data": self.destination_id is not None}


def _months(value: str | None) -> list[int] | None:
    import json

    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = [part for part in str(value).split(",") if part.strip()]
    try:
        return [int(month) for month in parsed]
    except (TypeError, ValueError):
        return None


def _from_destination(destination: Destination, resolved_by: str) -> City:
    return City(
        name=destination.name, country=destination.country, region=destination.region, lat=destination.lat, lon=destination.lon,
        timezone=destination.timezone, destination_id=destination.id, resolved_by=resolved_by,
        season_profile=destination.season_profile, peak_months=_months(destination.peak_months), currency=destination.currency,
        country_code=destination.country_code,
    )


def city_for_destination(destination_id: str | None) -> City | None:
    destination = destination_by_id(destination_id)
    return _from_destination(destination, "destination") if destination else None


def resolve_city(*, destination_id: str | None = None, lat: float | None = None, lon: float | None = None) -> tuple[City | None, list[dict[str, str]]]:
    """Resolve the city for a chosen destination or a coordinate. Returns (city, provider_errors)."""
    if destination_id:
        city = city_for_destination(destination_id)
        if city:
            return city, []
    if lat is None or lon is None:
        return None, []
    inside = nearest_destination(lat, lon)
    if inside:
        return _from_destination(inside, "coverage"), []
    named, errors = reverse_geocode(lat, lon)
    if not named:
        return None, errors
    city_name = named.get("city") or named.get("name")
    if not city_name:
        return None, errors
    wanted, country = normalize(city_name), normalize(named.get("country") or "")
    for destination in all_destinations():
        names = {normalize(destination.name), *(normalize(alias) for alias in destination_aliases(destination))}
        if wanted in names and (not country or normalize(destination.country or "") == country) and haversine_km(lat, lon, destination.lat, destination.lon) <= SAME_CITY_KM:
            return _from_destination(destination, "geocoder_match"), errors
    return City(name=city_name, country=named.get("country"), region=named.get("region"), lat=lat, lon=lon, resolved_by="geocoder"), errors


def city_from_geo(geo: Any) -> tuple[City | None, list[dict[str, str]]]:
    """The city for a request: a user-selected place wins over GPS; GPS is resolved to its city."""
    place = getattr(geo, "active_destination", None)
    if place is not None:
        return city_from_place(place), []
    location = getattr(geo, "user_location", None)
    if location is not None:
        return resolve_city(lat=location.lat, lon=location.lon)
    return None, []


def city_from_place(place: Any) -> City:
    """A chosen or named place (a stored destination or a geocoded one) as a city."""
    if place.destination_id:
        city = city_for_destination(place.destination_id)
        if city:
            return city
    return City(name=place.city or place.name, country=place.country, region=place.region, lat=place.lat, lon=place.lon, timezone=place.timezone, resolved_by="geocoder")


def country_code(city: City) -> str | None:
    """ISO alpha-2 for a city: its stored code, else looked up from the country name."""
    if city.country_code:
        return city.country_code.upper()
    from app.core.rules import load_rules

    return load_rules("countries")["names"].get(normalize(city.country or "")) if city.country else None
