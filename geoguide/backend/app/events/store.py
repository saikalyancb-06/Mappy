"""Stored event records: classification for loaders, the city clause, and "associated" festivals.

Dated stored records are served by ``providers.stored.StoredEventProvider``. A festival
that is only *associated* with a city (no confirmed dates for the period) is returned by
``associated_festivals`` and must always be labelled as unconfirmed, never as happening.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import or_, select

from app.core.dates import DateRange
from app.core.rules import load_rules
from app.db.models import EventFestival
from app.db.session import SessionLocal
from app.events.taxonomy import categorise
from app.geo.city import City

INACTIVE = {"cancelled", "canceled", "postponed", "inactive"}


def classify(title: str, summary: str | None = None, dataset_category: str | None = None, recurring: bool = False) -> tuple[str, str]:
    """(event_type, category) for loaders: 'festival' for recurring/cultural celebrations, else 'event'."""
    category, _, kind = categorise(title, summary, "dataset", [dataset_category] if dataset_category else None)
    if recurring and kind != "festival" and category in {"festivals", "religious"}:
        kind = "festival"
    return kind, category


def city_clause(city: City):
    clauses = [EventFestival.city_key == city.key]
    if city.destination_id:
        clauses.append(EventFestival.destination_id == city.destination_id)
    return or_(*clauses)


def _months(value: str | None) -> list[int] | None:
    try:
        months = json.loads(value) if value else None
        return [int(m) for m in months] if isinstance(months, list) else None
    except (TypeError, ValueError):
        return None


def associated_festivals(city: City, window: DateRange) -> list[dict[str, Any]]:
    """Festivals tied to the city with no confirmed dates, usually held in the window's months."""
    rules = load_rules("events")
    months: set[int] = set()
    day = window.start
    while day <= window.end and len(months) < 12:
        months.add(day.month)
        day = date.fromordinal(day.toordinal() + 1)
    with SessionLocal() as db:
        rows = db.scalars(select(EventFestival).where(city_clause(city), EventFestival.start_date.is_(None))).all()
    out = []
    for row in rows:
        typical = _months(row.typical_months)
        if not typical or not months & set(typical) or (row.status or "active").lower() in INACTIVE:
            continue
        category, _, _ = categorise(row.title, row.summary, "dataset", [row.category] if row.category else None)
        out.append({
            "id": row.id, "name": row.title, "title": row.title, "description": row.summary, "summary": row.summary, "type": "festival",
            "category": category, "group_label": rules["categories"].get(category, rules["categories"]["other"])["label"],
            "typical_months": typical, "recurrence": row.recurrence, "significance": row.significance, "traditions": row.traditions, "etiquette": row.etiquette,
            "timing": "usually_this_time_of_year", "start_date": None, "end_date": None, "venue": None, "distance_km": None,
            "source": {"name": row.source, "url": row.source_url, "kind": "stored_curated", "last_verified_at": row.last_verified_at},
            "confidence": row.confidence, "confidence_label": "unconfirmed", "kind": "associated_festival",
        })
    return out
