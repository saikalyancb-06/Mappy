"""Normalised event records and the event query.

Every provider maps its own format into ``NormalisedEvent``; everything downstream
(validation, de-duplication, confidence, ranking, the API) works on this one shape.
Times are timezone-aware datetimes in the destination's timezone, never raw strings.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.dates import DateRange
from app.core.rules import load_rules
from app.geo.city import City


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


@dataclass
class EventSource:
    provider: str  # stored | ticketmaster | google_events | web
    name: str  # human-readable, e.g. "Ticketmaster", "Karnataka Tourism (tourism.example.gov.in)"
    kind: str  # key into events.json "sources" reliability
    reliability: float
    source_event_id: str | None = None
    url: str | None = None
    retrieved_at: datetime | None = None
    last_updated: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "name": self.name, "kind": self.kind, "reliability": self.reliability, "source_event_id": self.source_event_id, "url": self.url, "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None, "last_updated": self.last_updated}


@dataclass
class NormalisedEvent:
    title: str
    start: datetime  # aware, destination timezone
    timezone: str
    source: EventSource
    end: datetime | None = None
    all_day: bool = False
    time_known: bool = False  # the source gave a start time, not just a date
    description: str | None = None
    category: str = "other"
    categories: list[str] = field(default_factory=list)
    type: str = "event"  # festival (recurring/cultural) | event
    venue_name: str | None = None
    venue_address: str | None = None
    venue_place_id: str | None = None  # resolved against GeoGuide's places
    lat: float | None = None
    lon: float | None = None
    city: str | None = None
    locality: str | None = None
    event_url: str | None = None
    ticket_url: str | None = None
    image_url: str | None = None
    price_kind: str = "unknown"  # free | paid | donation | unknown
    price_min: str | None = None  # decimal text
    price_max: str | None = None
    currency: str | None = None
    organizer: str | None = None
    recurrence: str | None = None
    significance: str | None = None
    traditions: str | None = None
    etiquette: str | None = None
    expected_footfall: int | None = None
    sources: list[EventSource] = field(default_factory=list)  # every source that reported it (after de-duplication)
    confidence: float = 0.0
    confidence_label: str = "low"
    confidence_notes: list[str] = field(default_factory=list)
    freshness: str = "fresh"  # fresh | recent | stale | expired | stored
    distance_km: float | None = None
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.sources:
            self.sources = [self.source]

    @property
    def id(self) -> str:
        key = f"{self.source.provider}|{self.source.source_event_id or ''}|{self.title}|{self.start.date()}"
        return "ev_" + hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def effective_end(self) -> datetime:
        if self.end:
            return self.end
        if self.all_day or not self.time_known:
            return datetime.combine(self.start.date(), time(23, 59), tzinfo=self.start.tzinfo)
        return self.start + timedelta(minutes=load_rules("events")["freshness"]["default_duration_min"])

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start <= end and self.effective_end >= start

    def as_dict(self, window: DateRange | None = None) -> dict[str, Any]:
        rules = load_rules("events")
        label = rules["categories"].get(self.category, rules["categories"]["other"])["label"]
        start_day, end_day = self.start.date(), self.effective_end.date()
        primary = self.sources[0]
        return {
            "id": self.id, "name": self.title, "title": self.title, "description": self.description, "summary": self.description,
            "type": self.type, "category": self.category, "categories": self.categories or [self.category], "group_label": label,
            "start": self.start.isoformat(), "end": self.end.isoformat() if self.end else None, "timezone": self.timezone,
            "all_day": self.all_day or not self.time_known, "start_date": start_day.isoformat(), "end_date": end_day.isoformat(),
            "start_time": self.start.strftime("%H:%M") if self.time_known and not self.all_day else None,
            "multi_day": end_day > start_day, "on_selected_date": bool(window and start_day <= window.start <= end_day),
            "venue": {"name": self.venue_name, "address": self.venue_address, "place_id": self.venue_place_id, "lat": self.lat, "lon": self.lon} if (self.venue_name or self.lat is not None) else None,
            "city": self.city, "locality": self.locality, "distance_km": self.distance_km,
            "event_url": self.event_url, "ticket_url": self.ticket_url, "image_url": self.image_url,
            "price": {"kind": self.price_kind, "min": self.price_min, "max": self.price_max, "currency": self.currency},
            "is_ticketed": True if self.price_kind == "paid" else False if self.price_kind == "free" else None,
            "ticket_price": self.price_min, "currency": self.currency,
            "organizer": self.organizer, "recurrence": self.recurrence, "significance": self.significance, "traditions": self.traditions, "etiquette": self.etiquette,
            "expected_footfall": self.expected_footfall, "crowded": bool(self.expected_footfall and self.expected_footfall >= rules["crowd_footfall_high"]),
            "source": {"name": primary.name, "url": primary.url or self.event_url, "kind": primary.kind, "provider": primary.provider, "retrieved_at": primary.retrieved_at.isoformat() if primary.retrieved_at else None, "last_verified_at": (primary.retrieved_at.isoformat() if primary.retrieved_at else primary.last_updated)},
            "sources": [s.as_dict() for s in self.sources],
            "confidence": round(self.confidence, 3), "confidence_label": self.confidence_label, "confidence_notes": self.confidence_notes,
            "freshness": self.freshness, "score": round(self.score, 4), "reasons": self.reasons,
            "kind": "event", "timing": "dated",
        }


@dataclass
class EventQuery:
    city: City
    window: DateRange
    start: datetime  # aware, destination timezone
    end: datetime
    centre: tuple[float, float]
    radius_km: float
    mode: str = "destination"  # destination | near_me
    text: str | None = None  # keywords from the request, if any
    categories: list[str] = field(default_factory=list)
    free_only: bool = False
    festival_only: bool = False
    user: dict[str, Any] = field(default_factory=dict)  # profile incl. user_id, for vibe compatibility
    user_point: tuple[float, float] | None = None
    limit: int = 40
    include_live: bool = True

    @property
    def tz(self) -> ZoneInfo:
        return self.start.tzinfo  # type: ignore[return-value]

    def cache_key(self, provider: str) -> str:
        return "|".join([provider, self.city.key, f"{self.centre[0]:.3f},{self.centre[1]:.3f}", f"{self.radius_km:g}", self.start.isoformat(), self.end.isoformat(), ",".join(sorted(self.categories)), (self.text or "").lower(), str(self.free_only), str(self.festival_only)])


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return datetime.combine(day, time(0, 0), tzinfo=tz), datetime.combine(day, time(23, 59, 59), tzinfo=tz)
