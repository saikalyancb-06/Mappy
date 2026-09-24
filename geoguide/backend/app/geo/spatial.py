"""Spatial candidate generation.

The database performs the geographic filtering: PostGIS ``ST_DWithin`` /
``ST_Distance`` on Postgres, an indexed bounding-box prefilter plus exact
haversine on SQLite. The LLM never decides proximity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import or_, select, text

from app.db.models import Poi
from app.db.session import CAPABILITIES, SessionLocal
from app.geo.distance import bounding_box, haversine_km
from app.models import Candidate, candidate_from_poi


@dataclass
class SpatialQuery:
    lat: float
    lon: float
    radius_km: float
    categories: Iterable[str] | None = None  # hard filter on pois.category
    kinds: Iterable[str] | None = None  # hard filter on pois.kind
    exclude_kinds: Iterable[str] | None = None
    destination_id: str | None = None
    limit: int = 200


def nearby(query: SpatialQuery) -> list[Candidate]:
    categories = sorted(set(query.categories or []))
    kinds = sorted(set(query.kinds or []))
    exclude = sorted(set(query.exclude_kinds or []))
    radius_km = max(0.05, float(query.radius_km))
    with SessionLocal() as db:
        if CAPABILITIES.get("postgis"):
            conditions = ["ST_DWithin(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)"]
            params: dict = {"lat": query.lat, "lon": query.lon, "radius_m": radius_km * 1000, "limit": query.limit}
            if categories:
                conditions.append("category = ANY(:categories)")
                params["categories"] = categories
            if kinds:
                conditions.append("kind = ANY(:kinds)")
                params["kinds"] = kinds
            if exclude:
                conditions.append("NOT (kind = ANY(:exclude))")
                params["exclude"] = exclude
            if query.destination_id:
                conditions.append("(destination_id = :destination_id OR destination_id IS NULL)")
                params["destination_id"] = query.destination_id
            sql = text(
                "SELECT id, ST_Distance(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000.0 AS distance_km "
                f"FROM pois WHERE {' AND '.join(conditions)} ORDER BY distance_km LIMIT :limit"
            )
            rows = db.execute(sql, params).all()
            distances = {row.id: float(row.distance_km) for row in rows}
            pois = db.scalars(select(Poi).where(Poi.id.in_(list(distances)))).all() if distances else []
            ordered = sorted(pois, key=lambda poi: distances[poi.id])
            return [candidate_from_poi(poi, distances[poi.id]) for poi in ordered]

        min_lat, max_lat, min_lon, max_lon = bounding_box(query.lat, query.lon, radius_km)
        statement = select(Poi).where(Poi.lat.between(min_lat, max_lat), Poi.lon.between(min_lon, max_lon))
        if categories:
            statement = statement.where(Poi.category.in_(categories))
        if kinds:
            statement = statement.where(Poi.kind.in_(kinds))
        if exclude:
            statement = statement.where(Poi.kind.not_in(exclude))
        if query.destination_id:
            statement = statement.where(or_(Poi.destination_id == query.destination_id, Poi.destination_id.is_(None)))
        results = []
        for poi in db.scalars(statement).all():
            distance = haversine_km(query.lat, query.lon, poi.lat, poi.lon)
            if distance <= radius_km:
                results.append(candidate_from_poi(poi, distance))
        results.sort(key=lambda candidate: candidate.distance_km or 0.0)
        return results[: query.limit]


def get_pois(ids: Iterable[str], reference: tuple[float, float] | None = None) -> list[Candidate]:
    ids = list(ids)
    if not ids:
        return []
    with SessionLocal() as db:
        pois = db.scalars(select(Poi).where(Poi.id.in_(ids))).all()
    by_id = {poi.id: poi for poi in pois}
    out = []
    for poi_id in ids:
        poi = by_id.get(poi_id)
        if poi is None:
            continue
        distance = haversine_km(reference[0], reference[1], poi.lat, poi.lon) if reference else None
        out.append(candidate_from_poi(poi, distance))
    return out


def count_pois_near(lat: float, lon: float, radius_km: float) -> int:
    return len(nearby(SpatialQuery(lat=lat, lon=lon, radius_km=radius_km, limit=1000)))
