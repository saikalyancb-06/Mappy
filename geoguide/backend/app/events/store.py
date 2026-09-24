"""City-scoped, date-native events and festivals.

Retrieval is ``city = ? AND start_date <= range_end AND end_date >= range_start``,
never a radius. Distance from the traveller is only added afterwards, for display.

Two kinds of records live here:
* **festival**: recurring or cultural celebrations (curated knowledge or the dataset);
* **live**: concerts, exhibitions, matches, fairs… from live listings.

A festival that is only *associated* with a city (no confirmed dates for the
period) is returned separately as ``associated`` and is never presented as
happening on the selected date. Zero matching records means zero events.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import and_, or_, select

from app.core.dates import DateRange
from app.core.rules import load_rules
from app.core.text import normalize
from app.db.models import EventFestival
from app.db.session import SessionLocal
from app.geo.city import City
from app.geo.distance import haversine_km

INACTIVE = {"cancelled", "canceled", "postponed", "inactive"}


def _has_phrase(text: str, phrases: list[str]) -> bool:
    padded = f" {text} "
    return any(f" {normalize(phrase)} " in padded for phrase in phrases)


def classify(title: str, summary: str | None = None, dataset_category: str | None = None, recurring: bool = False) -> tuple[str, str]:
    """(event_type, category) from the listing's own words, with the dataset's category as a fallback."""
    rules = load_rules("events")
    text = normalize(f"{title} {summary or ''}")
    title_text = normalize(title)
    is_festival = _has_phrase(title_text, rules["festival_keywords"]) or (recurring and _has_phrase(title_text, ["festival", "utsav", "jatre"]) and not _has_phrase(title_text, rules["category_keywords"]["arts"] + rules["category_keywords"]["music"] + rules["category_keywords"]["food"]))
    if is_festival:
        return "festival", "festival"
    for category, words in rules["category_keywords"].items():
        if category != "festival" and _has_phrase(title_text, words):
            return "live", category
    mapped = rules["dataset_category_map"].get(dataset_category or "")
    if mapped:
        return "live", mapped
    for category, words in rules["category_keywords"].items():
        if category != "festival" and _has_phrase(text, words):
            return "live", category
    return "live", "festival" if _has_phrase(title_text, ["festival", "fest"]) else "other"


def _months(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        months = json.loads(value)
        return [int(m) for m in months] if isinstance(months, list) else None
    except (TypeError, ValueError):
        return None


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def event_dict(row: EventFestival, window: DateRange | None, user_point: tuple[float, float] | None) -> dict[str, Any]:
    rules = load_rules("events")
    event_type = row.event_type or classify(row.title, row.summary, recurring=bool(row.typical_months))[0]
    category = row.category or classify(row.title, row.summary, recurring=bool(row.typical_months))[1]
    start, end = _day(row.start_date), _day(row.end_date) or _day(row.start_date)
    distance = round(haversine_km(user_point[0], user_point[1], row.venue_lat, row.venue_lon), 2) if user_point and row.venue_lat is not None and row.venue_lon is not None else None
    live_source = (row.data_source_id or "").startswith(("serpapi", "ticketmaster"))
    return {
        "id": row.id,
        "name": row.title,
        "title": row.title,  # kept for older clients
        "type": event_type,
        "category": category,
        "group_label": rules["groups"].get(category, rules["groups"]["other"])["label"],
        "description": row.summary,
        "summary": row.summary,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "multi_day": bool(start and end and end > start),
        "on_selected_date": bool(window and start and end and start <= window.start <= end),
        "typical_months": _months(row.typical_months),
        "recurrence": row.recurrence,
        "season": row.season,
        "venue": {"name": row.venue_name, "lat": row.venue_lat, "lon": row.venue_lon} if (row.venue_name or row.venue_lat is not None) else None,
        "distance_km": distance,
        "is_ticketed": row.is_ticketed,
        "ticket_price": row.ticket_price,
        "currency": row.currency,
        "expected_footfall": row.expected_footfall,
        "crowded": bool(row.expected_footfall and row.expected_footfall >= rules["crowd_footfall_high"]),
        "significance": row.significance,
        "traditions": row.traditions,
        "etiquette": row.etiquette,
        "status": row.status or "active",
        "source": {
            "name": row.source,
            "url": row.source_url,
            "kind": "live" if live_source else ("dataset" if row.data_source_id == "ps13" else "curated"),
            "published_at": row.source_published_at,
            "last_verified_at": row.last_verified_at or (row.updated_at.isoformat() + "Z" if row.updated_at else None),
        },
        "confidence": row.confidence,
        "kind": "stored_event",
        "timing": "dated" if start else "associated",
    }


def _city_clause(city: City):
    clauses = [EventFestival.city_key == city.key]
    if city.destination_id:
        clauses.append(EventFestival.destination_id == city.destination_id)
    return or_(*clauses)


def events_in_city(city: City, window: DateRange, user_point: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """Dated events in the city that overlap the window (multi-day events included)."""
    start, end = window.start.isoformat(), window.end.isoformat()
    with SessionLocal() as db:
        rows = db.scalars(select(EventFestival).where(
            _city_clause(city),
            EventFestival.start_date.is_not(None),
            EventFestival.start_date <= end + "T99",  # ISO text compare; tolerates datetime strings
            or_(and_(EventFestival.end_date.is_not(None), EventFestival.end_date >= start), and_(EventFestival.end_date.is_(None), EventFestival.start_date >= start)),
        )).all()
    events = [event_dict(row, window, user_point) for row in rows if (row.status or "active").lower() not in INACTIVE]
    # Guard against malformed dates slipping past the text comparison.
    events = [e for e in events if e["start_date"] and e["start_date"] <= end and (e["end_date"] or e["start_date"]) >= start]
    events.sort(key=lambda e: (not e["on_selected_date"], e["type"] != "festival", e["start_date"], e["name"]))
    return events


def associated_festivals(city: City, window: DateRange) -> list[dict[str, Any]]:
    """Festivals tied to the city with no confirmed dates, usually held in the window's months.

    These are cultural context, not listings: callers must say the dates are unconfirmed.
    """
    months = set()
    day = window.start
    while day <= window.end and len(months) < 12:
        months.add(day.month)
        day = date.fromordinal(day.toordinal() + 1)
    with SessionLocal() as db:
        rows = db.scalars(select(EventFestival).where(_city_clause(city), EventFestival.start_date.is_(None))).all()
    out = []
    for row in rows:
        typical = _months(row.typical_months)
        if typical and months & set(typical) and (row.status or "active").lower() not in INACTIVE:
            item = event_dict(row, None, None)
            item["timing"] = "usually_this_time_of_year"
            out.append(item)
    return out
