"""City registry: one canonical record per destination, with enrichment state.

``destinations`` is the registry (it is extended, not duplicated). Identity is
deterministic: an existing stored city is found by id, by name/alias within its
country and extent, or by containing the coordinates; only then is a new city
created, with an id derived from its normalised name, country and rounded
coordinates, so "Bangalore, India" and "Bengaluru, India" are one city and a city
selected twice is never created twice.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.rules import load_rules
from app.core.text import normalize
from app.db.models import CityEnrichmentComponent, Destination, EntityAlias, Poi
from app.db.session import SessionLocal
from app.geo.distance import haversine_km, valid_coordinates
from app.geo.geocoding import city_extent_km, destination_aliases, invalidate_destination_index, nearest_destination

STATUSES = ("NOT_STARTED", "QUEUED", "ENRICHING", "PARTIAL", "READY", "FAILED", "STALE")
SAME_CITY_KM = 60.0
NEW_CITY_COVERAGE_KM = 8.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_city_id(name: str, country: str | None, lat: float, lon: float) -> str:
    key = f"{normalize(name)}|{normalize(country or '')}|{round(lat, 1):.1f}|{round(lon, 1):.1f}"
    return "city-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def find_city(name: str | None, country: str | None, lat: float | None, lon: float | None) -> Destination | None:
    """An already-registered city for this name/place, if any."""
    if lat is not None and lon is not None and valid_coordinates(lat, lon):
        inside = nearest_destination(float(lat), float(lon))
        if inside is not None:
            return inside
    wanted, wanted_country = normalize(name or ""), normalize(country or "")
    if not wanted:
        return None
    with SessionLocal() as db:
        destinations = db.scalars(select(Destination)).all()
        aliases = db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "destination", EntityAlias.normalized == wanted)).all()
    alias_ids = {a.entity_id for a in aliases}
    for destination in destinations:
        names = {normalize(destination.name), normalize(destination.normalized_name or ""), *(normalize(a) for a in destination_aliases(destination))}
        if wanted not in names and destination.id not in alias_ids:
            continue
        if wanted_country and destination.country and normalize(destination.country) != wanted_country:
            continue
        if lat is not None and lon is not None and haversine_km(float(lat), float(lon), destination.lat, destination.lon) > max(SAME_CITY_KM, city_extent_km(destination)):
            continue
        return destination
    return None


def get_or_create_city(place: dict[str, Any]) -> tuple[Destination, bool]:
    """(city, created). ``place`` needs a name and coordinates; destination_id wins when it exists."""
    destination_id = place.get("destination_id") or place.get("id")
    if destination_id:
        with SessionLocal() as db:
            found = db.get(Destination, destination_id)
        if found is not None:
            return found, False
    lat, lon = place.get("lat"), place.get("lon")
    name = str(place.get("city") or place.get("name") or "").strip()
    if not name or not valid_coordinates(lat, lon):
        raise ValueError("A city needs a name and valid coordinates.")
    existing = find_city(name, place.get("country"), float(lat), float(lon))
    if existing is not None:
        return existing, False
    from app.geo.city import country_code, City  # local import: city imports geocoding

    new_id = canonical_city_id(name, place.get("country"), float(lat), float(lon))
    with SessionLocal() as db:
        again = db.get(Destination, new_id)
        if again is not None:
            return again, False
        code = country_code(City(name=name, country=place.get("country"), region=place.get("region"), lat=float(lat), lon=float(lon), country_code=place.get("country_code")))
        destination = Destination(
            id=new_id, name=name, normalized_name=normalize(name), region=place.get("region"), country=place.get("country"), country_code=code,
            lat=float(lat), lon=float(lon), coverage_radius_km=float(place.get("coverage_radius_km") or NEW_CITY_COVERAGE_KM), timezone=place.get("timezone"),
            languages="[]", source=place.get("source") or "geocoder", curated=False, enrichment_status="NOT_STARTED", data_version=0,
            population=place.get("population"), boundary_radius_km=place.get("boundary_km"),
            external_ids=json.dumps({k: v for k, v in (place.get("external_ids") or {}).items() if v}) or None, created_at=_now(), updated_at=_now(),
        )
        db.add(destination)
        db.add(EntityAlias(entity_type="destination", entity_id=new_id, alias=name, normalized=normalize(name)))
        db.commit()
        db.refresh(destination)
    invalidate_destination_index()
    return destination, True


def components_config() -> dict[str, dict[str, Any]]:
    return load_rules("city_intelligence")["components"]


def component_rows(destination_id: str) -> dict[str, CityEnrichmentComponent]:
    with SessionLocal() as db:
        rows = db.scalars(select(CityEnrichmentComponent).where(CityEnrichmentComponent.destination_id == destination_id)).all()
    return {row.component: row for row in rows}


def _is_fresh(row: CityEnrichmentComponent | None, now: datetime) -> bool:
    return bool(row and row.status in {"done", "empty"} and (row.expires_at is None or row.expires_at > now))


def evaluate_status(destination: Destination, now: datetime | None = None) -> dict[str, Any]:
    """Derive the city's state from its components (never all-or-nothing)."""
    now = now or _now()
    config = components_config()
    rows = component_rows(destination.id)
    fresh = {name for name in config if _is_fresh(rows.get(name), now)}
    expired = {name for name, row in rows.items() if row.status in {"done", "empty"} and row.expires_at is not None and row.expires_at <= now}
    failed = {name for name, row in rows.items() if row.status in {"failed", "unavailable"}}
    running = {name for name, row in rows.items() if row.status == "running" and row.updated_at and now - row.updated_at <= timedelta(minutes=20)}
    required = {name for name, spec in config.items() if spec.get("required")}
    if destination.enrichment_status in {"QUEUED", "ENRICHING"} and (running or not rows):
        status = destination.enrichment_status
    elif fresh == set(config):
        status = "READY"
    elif expired & required or (expired and not (fresh & required)):
        status = "STALE"
    elif fresh:
        status = "PARTIAL"
    elif failed:
        status = "FAILED"
    else:
        status = "NOT_STARTED"
    with SessionLocal() as db:
        place_count = db.query(Poi).filter(Poi.destination_id == destination.id).count()
    return {
        "status": status,
        "components": {name: {"status": rows[name].status if name in rows else "pending", "class": spec["class"], "required": bool(spec.get("required")), "items": rows[name].item_count if name in rows else 0,
                              "last_run_at": rows[name].last_run_at.isoformat() + "Z" if name in rows and rows[name].last_run_at else None,
                              "expires_at": rows[name].expires_at.isoformat() + "Z" if name in rows and rows[name].expires_at else None,
                              "error": rows[name].error if name in rows else None} for name, spec in config.items()},
        "progress": int(100 * len([n for n in config if n in rows and rows[n].status not in {"pending", "running"}]) / max(1, len(config))),
        "place_count": place_count,
        "fresh": sorted(fresh), "stale": sorted(expired), "failed": sorted(failed),
    }


def missing_components(destination: Destination, now: datetime | None = None) -> list[str]:
    """Components to (re)collect: never run, expired, or failed long enough ago to retry."""
    now = now or _now()
    retry = timedelta(hours=load_rules("city_intelligence")["freshness_days"]["retry_failed_after_hours"])
    rows = component_rows(destination.id)
    wanted = []
    for name in components_config():
        row = rows.get(name)
        if row is None or row.status == "pending":
            wanted.append(name)
        elif row.status in {"done", "empty"} and row.expires_at is not None and row.expires_at <= now:
            wanted.append(name)
        elif row.status in {"failed", "unavailable"} and (row.last_run_at is None or now - row.last_run_at >= retry):
            wanted.append(name)
        elif row.status == "running" and row.updated_at and now - row.updated_at > timedelta(minutes=20):
            wanted.append(name)  # a run that died with the process
    return wanted


def set_status(destination_id: str, status: str, **fields: Any) -> None:
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
        if destination is None:
            return
        destination.enrichment_status = status
        for key, value in fields.items():
            setattr(destination, key, value)
        destination.updated_at = _now()
        db.commit()


def city_dict(destination: Destination) -> dict[str, Any]:
    return {
        "id": destination.id, "destination_id": destination.id, "name": destination.name, "region": destination.region, "country": destination.country,
        "country_code": destination.country_code, "lat": destination.lat, "lon": destination.lon, "timezone": destination.timezone,
        "coverage_radius_km": destination.coverage_radius_km, "curated": bool(destination.curated), "kind": "destination",
        "enrichment_status": destination.enrichment_status or "NOT_STARTED", "data_version": destination.data_version or 0,
        "last_enriched_at": destination.last_enriched_at.isoformat() + "Z" if destination.last_enriched_at else None,
    }
