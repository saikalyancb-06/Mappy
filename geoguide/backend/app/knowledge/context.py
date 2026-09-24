"""Destination-scoped live context: safety advisories and events.

Advisories keep their stored severity and validity; nothing is invented when
no advisory exists. Weather-derived notices are labelled as derived from the
forecast, not as official advisories.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import EventFestival, SafetyAdvisory
from app.db.session import SessionLocal
from app.search.normalizer import web_evidence
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client

_SEVERITY_ORDER = {"high": 0, "moderate": 1, "low": 2, "info": 3}


def _months(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        months = json.loads(value)
        return [int(m) for m in months] if isinstance(months, list) else None
    except (TypeError, ValueError):
        return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def active_advisories(destination_id: str | None, on: date, poi_ids: list[str] | None = None) -> list[dict[str, Any]]:
    if not destination_id:
        return []
    with SessionLocal() as db:
        rows = db.scalars(select(SafetyAdvisory).where(SafetyAdvisory.destination_id == destination_id)).all()
    active = []
    for row in rows:
        months = _months(row.active_months)
        if months and on.month not in months:
            continue
        start, end = _date(row.valid_from), _date(row.valid_to)
        if (start and on < start) or (end and on > end):
            continue
        if row.poi_id and poi_ids is not None and row.poi_id not in poi_ids:
            continue
        active.append({
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "category": row.category,
            "severity": row.severity,
            "valid_from": row.valid_from,
            "valid_to": row.valid_to,
            "active_months": months,
            "poi_id": row.poi_id,
            "source": row.source,
            "source_url": row.source_url,
            "kind": "advisory",
        })
    active.sort(key=lambda item: _SEVERITY_ORDER.get(item["severity"], 9))
    return active


def weather_notices(weather: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Notices derived from forecast signals, clearly labelled as such."""
    if not weather or weather.get("status") != "ok" or not weather.get("day"):
        return []
    day = weather["day"]
    notices = []
    if "heat" in (day.get("signals") or []):
        notices.append({"id": "weather-heat", "title": "High heat forecast", "body": f"Feels-like temperatures up to {round(day['apparent_max_c'])}°C on {day['date']}. Plan open-air sites for early morning or late afternoon.", "severity": "moderate", "category": "heat", "kind": "weather_derived", "source": "Open-Meteo forecast"})
    if "rain" in (day.get("signals") or []):
        probability = day.get("precipitation_probability_max")
        notices.append({"id": "weather-rain", "title": "Rain likely", "body": f"{day['summary']} with up to {probability}% chance of precipitation on {day['date']}." if probability is not None else f"{day['summary']} on {day['date']}.", "severity": "low", "category": "rain", "kind": "weather_derived", "source": "Open-Meteo forecast"})
    return notices


def events_for(destination_id: str | None, start: date, days: int = 7) -> list[dict[str, Any]]:
    if not destination_id:
        return []
    with SessionLocal() as db:
        rows = db.scalars(select(EventFestival).where(EventFestival.destination_id == destination_id)).all()
    window_months = {date.fromordinal(start.toordinal() + offset).month for offset in range(days)}
    upcoming, other = [], []
    for row in rows:
        begin, finish = _date(row.start_date), _date(row.end_date) or _date(row.start_date)
        months = _months(row.typical_months)
        item = {"id": row.id, "title": row.title, "summary": row.summary, "start_date": row.start_date, "end_date": row.end_date, "typical_months": months, "recurrence": row.recurrence, "source": row.source, "source_url": row.source_url, "confidence": row.confidence, "kind": "stored_event"}
        if begin and finish:
            item["timing"] = "dated"
            if begin <= date.fromordinal(start.toordinal() + days) and finish >= start:
                upcoming.append(item)
            continue
        if months and window_months & set(months):
            item["timing"] = "usually_this_time_of_year"
            upcoming.append(item)
        else:
            item["timing"] = "dates_vary" if not months else "other_season"
            other.append(item)
    return upcoming + other


def web_events(place_name: str, web: SerpApiClient | None = None) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    web = web or default_client
    if not web.configured:
        return [], {"source": "serpapi", "code": "web_search_unavailable", "message": "Web search is not configured."}
    try:
        response = web.search(f"events in {place_name}", engine="google_events", limit=6)
    except SearchProviderError as exc:
        return [], exc.as_dict()
    return [web_evidence(result, response.retrieved_at) for result in response.results], None


def today_in(timezone_now: datetime) -> date:
    return timezone_now.date()
