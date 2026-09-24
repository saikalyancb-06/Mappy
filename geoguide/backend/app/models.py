"""Canonical in-memory representations shared by every pipeline stage.

Database rows, SerpApi results and OSM elements are all normalised into
``Candidate`` (a place) and ``Evidence`` (a citable fact) so ranking,
entity resolution and answer generation never deal with provider shapes.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.text import normalize
from app.geo import opening_hours

# Relative trust per source type; overridable in data/config/ranking.json.
DEFAULT_SOURCE_CONFIDENCE = {"curated": 0.95, "database": 0.9, "osm": 0.75, "serpapi_maps": 0.7, "serpapi_web": 0.5}


@dataclass
class SourceRef:
    source_type: str  # curated | database | osm | serpapi_maps | serpapi_web | open_meteo
    source: str
    source_url: str | None = None
    source_id: str | None = None
    retrieved_at: str | None = None
    confidence: float | None = None


@dataclass
class Candidate:
    id: str
    name: str
    category: str | None
    kind: str | None
    lat: float | None
    lon: float | None
    destination_id: str | None = None
    address: str | None = None
    neighborhood: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    rating: float | None = None
    review_count: int | None = None
    opening_hours: dict[str, Any] | None = None
    opening_hours_text: str | None = None
    open_status: str = "unknown"  # open | closed | unknown
    open_detail: dict[str, Any] = field(default_factory=dict)
    entry_fee: float | None = None
    entry_fee_foreign: float | None = None
    fee_currency: str | None = None
    fee_notes: str | None = None
    price_level: int | None = None
    visit_duration_min: int | None = None
    step_free: bool | None = None
    accessibility_notes: str | None = None
    indoor: bool | None = None
    walking_effort: str | None = None
    best_time: str | None = None
    phone: str | None = None
    website: str | None = None
    coordinate_precision: str | None = None
    sources: list[SourceRef] = field(default_factory=list)
    confidence: float = 0.5
    distance_km: float | None = None
    scores: dict[str, float] = field(default_factory=dict)
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def normalized_name(self) -> str:
        return normalize(self.name)

    @property
    def primary_source(self) -> SourceRef | None:
        return self.sources[0] if self.sources else None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        primary = self.primary_source
        data["source"] = primary.source if primary else None
        data["source_type"] = primary.source_type if primary else None
        data["source_url"] = primary.source_url if primary else None
        return data


@dataclass
class Evidence:
    id: str  # stable citation id within one answer, e.g. "E3"
    source_type: str  # poi | knowledge | weather | safety | event | web
    title: str
    content: str
    source: str | None = None
    source_url: str | None = None
    source_id: str | None = None
    retrieved_at: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_from_poi(poi: Any, distance_km: float | None = None) -> Candidate:
    """Normalise a ``Poi`` ORM row."""
    hours = opening_hours.load(poi.opening_hours) or opening_hours.parse_osm(poi.opening_hours_raw)
    source_type = "curated" if (poi.source or "").startswith("curated") else ("osm" if (poi.source or "") == "OpenStreetMap" else "database")
    try:
        tags = json.loads(poi.tags or "[]")
    except ValueError:
        tags = []
    return Candidate(
        id=poi.id,
        name=poi.name,
        category=poi.category,
        kind=poi.kind,
        lat=poi.lat,
        lon=poi.lon,
        destination_id=poi.destination_id,
        address=poi.address,
        neighborhood=poi.neighborhood,
        description=poi.description,
        tags=tags if isinstance(tags, list) else [],
        rating=poi.rating,
        review_count=poi.review_count,
        opening_hours=hours,
        opening_hours_text=opening_hours.describe(hours) or poi.opening_hours_raw,
        entry_fee=poi.entry_fee,
        entry_fee_foreign=poi.entry_fee_foreign,
        fee_currency=poi.fee_currency,
        fee_notes=poi.fee_notes,
        price_level=poi.price_level,
        visit_duration_min=poi.visit_duration_min,
        step_free=poi.step_free,
        accessibility_notes=poi.accessibility_notes,
        indoor=poi.indoor,
        walking_effort=poi.walking_effort,
        best_time=poi.best_time,
        phone=poi.phone,
        website=poi.website,
        coordinate_precision=poi.coordinate_precision,
        sources=[SourceRef(source_type=source_type, source=poi.source or "GeoGuide database", source_url=poi.source_url, source_id=poi.source_id, retrieved_at=poi.fetched_at.isoformat() + "Z" if poi.fetched_at else None, confidence=poi.confidence)],
        confidence=poi.confidence if poi.confidence is not None else DEFAULT_SOURCE_CONFIDENCE.get(source_type, 0.6),
        distance_km=round(distance_km, 3) if distance_km is not None else None,
    )
