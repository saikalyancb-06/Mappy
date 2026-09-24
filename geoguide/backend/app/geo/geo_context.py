"""Canonical GeoContext.

Keeps three things separate and explicit:

* ``user_location`` – where the traveller physically is (device GPS), with
  accuracy, timestamp and freshness. Missing GPS is ``unavailable``, never a
  default city.
* ``active_destination`` – the destination the traveller is exploring
  (selected in the app or carried over from the conversation).
* ``query_destination`` – a place named in the current question.

``reference`` is the single point + radius retrieval should use for this
question, chosen by explicit rules (explicit destination wins; "near me" means
GPS; otherwise the active destination; otherwise GPS).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import LOCATION_HARD_MAX_AGE_S, LOCATION_LOW_ACCURACY_M, LOCATION_SOFT_MAX_AGE_S
from app.core.rules import load_rules
from app.db.models import Destination
from app.db.session import SessionLocal
from app.geo.distance import haversine_km, valid_coordinates
from app.core.text import normalize
from app.geo.geocoding import ResolvedPlace, city_extent_km, destination_to_place, nearest_destination, resolve_place

DEFAULT_PLACE_RADIUS_KM = 2.0
CENTRE_SHARE_OF_CITY = 0.25  # "the centre" of a city is the inner quarter of its extent …
CENTRE_RADIUS_KM = (2.0, 6.0)  # … kept between a walkable 2 km and 6 km
_ALLOWED_SOURCES = {"device", "selected_map_location"}


class GeoContextError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class UserLocation:
    lat: float
    lon: float
    accuracy_m: float | None
    timestamp: str
    source: str
    age_s: float
    freshness: str  # fresh | stale
    accuracy_level: str  # good | low | unknown
    area_name: str | None = None
    inside_destination_id: str | None = None


@dataclass
class ActiveReference:
    origin: str  # user_location | query_destination | active_destination | entity
    lat: float
    lon: float
    label: str
    radius_km: float
    semantic: str  # near_me | in_destination | near_place
    destination_id: str | None = None

    @property
    def distance_label(self) -> str:
        """What distances are measured from, in words."""
        if self.origin == "user_location":
            return "you"
        return f"{self.label} centre" if self.semantic == "in_destination" else self.label


@dataclass
class GeoContext:
    user_location: UserLocation | None
    location_status: str  # available | stale | unavailable
    active_destination: ResolvedPlace | None = None
    query_destination: ResolvedPlace | None = None
    reference: ActiveReference | None = None
    reference_required: str | None = None  # why no reference could be chosen
    warnings: list[str] = field(default_factory=list)
    provider_errors: list[dict[str, str]] = field(default_factory=list)
    built_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def destination_for_answer(self) -> ResolvedPlace | None:
        return self.query_destination or self.active_destination

    def user_point(self) -> tuple[float, float] | None:
        return (self.user_location.lat, self.user_location.lon) if self.user_location else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_location": asdict(self.user_location) if self.user_location else None,
            "location_status": self.location_status,
            "active_destination": self.active_destination.as_dict() if self.active_destination else None,
            "query_destination": self.query_destination.as_dict() if self.query_destination else None,
            "reference": asdict(self.reference) if self.reference else None,
            "reference_required": self.reference_required,
            "warnings": self.warnings,
            "provider_errors": self.provider_errors,
            "built_at": self.built_at,
        }


def parse_user_location(raw: dict[str, Any] | None, now: datetime | None = None) -> tuple[UserLocation | None, str, list[str]]:
    """Validate a device location payload. Returns (location, status, warnings)."""
    if not raw:
        return None, "unavailable", []
    lat = raw.get("lat", raw.get("latitude"))
    lon = raw.get("lon", raw.get("longitude"))
    if not valid_coordinates(lat, lon):
        return None, "unavailable", ["The device location was invalid and was ignored."]
    source = str(raw.get("source") or "device")
    if source not in _ALLOWED_SOURCES:
        return None, "unavailable", ["The device location source was not recognised and was ignored."]
    now = now or datetime.now(timezone.utc)
    timestamp = raw.get("timestamp")
    if not timestamp:
        return None, "unavailable", ["The device location had no timestamp, so its freshness is unknown; it was ignored."]
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None, "unavailable", ["The device location timestamp was invalid; it was ignored."]
    age = (now - parsed).total_seconds()
    if age < -120:
        return None, "unavailable", ["The device location timestamp is in the future; it was ignored."]
    age = max(age, 0.0)
    if age > LOCATION_HARD_MAX_AGE_S:
        return None, "unavailable", [f"The last device location is {int(age // 60)} minutes old, too old to use."]
    accuracy = raw.get("accuracy_m", raw.get("accuracy_meters", raw.get("accuracy")))
    accuracy_m = float(accuracy) if isinstance(accuracy, (int, float)) and accuracy >= 0 else None
    warnings: list[str] = []
    freshness = "fresh"
    if age > LOCATION_SOFT_MAX_AGE_S:
        freshness = "stale"
        warnings.append(f"Your location was last updated {int(age // 60)} minutes ago.")
    accuracy_level = "unknown" if accuracy_m is None else ("low" if accuracy_m > LOCATION_LOW_ACCURACY_M else "good")
    if accuracy_level == "low":
        warnings.append(f"Your location is approximate (±{int(accuracy_m)} m).")
    location = UserLocation(lat=float(lat), lon=float(lon), accuracy_m=accuracy_m, timestamp=parsed.isoformat(), source=source, age_s=round(age, 1), freshness=freshness, accuracy_level=accuracy_level)
    inside = nearest_destination(location.lat, location.lon)
    if inside:
        location.inside_destination_id = inside.id
        location.area_name = inside.name
    return location, ("stale" if freshness == "stale" else "available"), warnings


def load_destination(raw: dict[str, Any] | str | None) -> tuple[ResolvedPlace | None, list[dict[str, str]]]:
    """Resolve the app's selected destination by id, or by name/coordinates."""
    if not raw:
        return None, []
    if isinstance(raw, str):
        raw = {"id": raw} if raw.startswith("dest-") else {"name": raw}
    destination_id = raw.get("id") or raw.get("destination_id")
    if destination_id:
        with SessionLocal() as db:
            destination = db.get(Destination, destination_id)
        if destination:
            return destination_to_place(destination), []
    name = raw.get("name")
    if valid_coordinates(raw.get("lat"), raw.get("lon")) and name:
        inside = nearest_destination(float(raw["lat"]), float(raw["lon"]))
        if inside:
            return destination_to_place(inside), []
        return ResolvedPlace(name=str(name), lat=float(raw["lat"]), lon=float(raw["lon"]), kind="geocoded", city=str(name), source=str(raw.get("source") or "client"), confidence=float(raw.get("confidence") or 0.7), coverage_radius_km=float(raw.get("coverage_radius_km") or 10.0)), []
    if name:
        return resolve_place(str(name))
    return None, []


def is_centre_phrase(mention: str | None) -> bool:
    """'city centre', 'downtown', 'the center' … mean the centre of the current city, not a place called that."""
    return bool(mention) and normalize(mention) in {normalize(p) for p in load_rules("geo")["centre_phrases"]}


def city_centre(active: ResolvedPlace | None, location: UserLocation | None) -> tuple[ResolvedPlace | None, list[dict[str, str]]]:
    """The centre of the city being explored, or of the city the traveller is in."""
    from app.geo.city import resolve_city  # local import: app.geo.city imports this module

    if active is not None:
        name, lat, lon, destination_id, region, country, tz = active.city or active.name, active.lat, active.lon, active.destination_id, active.region, active.country, active.timezone
        errors: list[dict[str, str]] = []
    elif location is not None:
        city, errors = resolve_city(lat=location.lat, lon=location.lon)
        if city is None:
            return None, errors
        name, lat, lon, destination_id, region, country, tz = city.name, city.lat, city.lon, city.destination_id, city.region, city.country, city.timezone
        if destination_id is None:
            # A city known only from reverse geocoding: its centre is where the geocoder places the city.
            place, more = resolve_place(city.name if not city.country else f"{city.name}, {city.country}", near=(location.lat, location.lon))
            errors = [*errors, *more]
            if place is None or place.kind != "geocoded":
                return None, errors
            lat, lon = place.lat, place.lon
    else:
        return None, []
    destination = destination_by_id(destination_id)
    extent = city_extent_km(destination) if destination else float(getattr(active, "coverage_radius_km", None) or 8.0)
    radius = round(min(CENTRE_RADIUS_KM[1], max(CENTRE_RADIUS_KM[0], CENTRE_SHARE_OF_CITY * extent)), 1)
    return ResolvedPlace(name=f"{name} centre", lat=lat, lon=lon, kind="centre", coverage_radius_km=radius, city=name, region=region, country=country, destination_id=destination_id, timezone=tz, source="city_centre", confidence=0.9), errors


def _destination_reference(place: ResolvedPlace, origin: str) -> ActiveReference:
    if place.kind == "centre":
        return ActiveReference(origin=origin, lat=place.lat, lon=place.lon, label=place.name, radius_km=float(place.coverage_radius_km or CENTRE_RADIUS_KM[0]), semantic="near_place", destination_id=place.destination_id)
    if place.kind == "poi":
        return ActiveReference(origin=origin, lat=place.lat, lon=place.lon, label=place.name, radius_km=DEFAULT_PLACE_RADIUS_KM, semantic="near_place", destination_id=place.destination_id)
    return ActiveReference(origin=origin, lat=place.lat, lon=place.lon, label=place.name, radius_km=float(place.coverage_radius_km or 10.0), semantic="in_destination", destination_id=place.destination_id)


def build_geo_context(
    *,
    user_location: dict[str, Any] | None,
    active_destination: dict[str, Any] | str | None,
    place_mention: str | None = None,
    spatial_relation: str | None = None,
    radius_km: float | None = None,
) -> GeoContext:
    location, status, warnings = parse_user_location(user_location)
    active, errors = load_destination(active_destination)
    context = GeoContext(user_location=location, location_status=status, active_destination=active, warnings=list(warnings), provider_errors=list(errors))

    if place_mention and is_centre_phrase(place_mention):
        centre, centre_errors = city_centre(active, location)
        context.provider_errors.extend(centre_errors)
        if centre:
            context.query_destination = centre
        else:
            context.warnings.append("Which city's centre? Choose a destination or turn on your location.")
            context.reference_required = "location_or_destination"
            return context
    elif place_mention:
        # Look names up around the destination being explored, else around the traveller.
        near = ((active.lat, active.lon) if active else None) or context.user_point()
        resolved, resolve_errors = resolve_place(place_mention, near=near)
        context.provider_errors.extend(resolve_errors)
        if resolved:
            context.query_destination = resolved
        else:
            context.warnings.append(f"I could not find a place called “{place_mention}” near here.")

    rules = load_rules("intents")
    near_me_radius = float(radius_km or rules["near_me_radius_km"])

    # Rule 2: "near me" means the physical location.
    if spatial_relation == "near_me":
        if location:
            context.reference = ActiveReference(origin="user_location", lat=location.lat, lon=location.lon, label=location.area_name or "your location", radius_km=near_me_radius, semantic="near_me", destination_id=location.inside_destination_id)
        else:
            context.reference_required = "user_location"
        return context

    # Rule 1: an explicit place in the question wins.
    if context.query_destination:
        reference = _destination_reference(context.query_destination, "query_destination")
        if spatial_relation == "near_place" and context.query_destination.kind not in {"destination", "centre"}:
            reference.semantic = "near_place"
            reference.radius_km = float(radius_km or DEFAULT_PLACE_RADIUS_KM)
        elif radius_km:
            reference.radius_km = float(radius_km)
        context.reference = reference
        return context
    if place_mention:
        context.reference_required = "resolvable_place"
        return context

    # A bare radius ("within 5 km") is measured from where the traveller is.
    if spatial_relation == "radius" and location:
        context.reference = ActiveReference(origin="user_location", lat=location.lat, lon=location.lon, label=location.area_name or "your location", radius_km=near_me_radius, semantic="near_me", destination_id=location.inside_destination_id)
        return context

    # Rule 3: the exploration destination persists across questions.
    if active:
        reference = _destination_reference(active, "active_destination")
        if radius_km:
            reference.radius_km = float(radius_km)
        context.reference = reference
        return context
    if location:
        context.reference = ActiveReference(origin="user_location", lat=location.lat, lon=location.lon, label=location.area_name or "your location", radius_km=near_me_radius, semantic="near_me", destination_id=location.inside_destination_id)
        return context
    context.reference_required = "location_or_destination"
    return context


def distance_from_user(context: GeoContext, lat: float | None, lon: float | None) -> float | None:
    point = context.user_point()
    if point is None or lat is None or lon is None:
        return None
    return round(haversine_km(point[0], point[1], lat, lon), 3)


def destination_by_id(destination_id: str | None) -> Destination | None:
    if not destination_id:
        return None
    with SessionLocal() as db:
        return db.get(Destination, destination_id)


def all_destinations() -> list[Destination]:
    with SessionLocal() as db:
        return list(db.scalars(select(Destination)).all())
