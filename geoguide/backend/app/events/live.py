"""Live event listings for a city and date range.

Source hierarchy (highest first): curated/dataset records → live event APIs
(Ticketmaster, where it has coverage) → web event search (Google Events via
SerpApi). The LLM is never a source of events.

Listings are turned into dated records only when their dates can be read and
overlap the requested range; anything else is dropped (and counted), never
guessed. Kept listings are stored with their source and verification time so
they carry evidence like every other record.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.config import TICKETMASTER_API_KEY, TICKETMASTER_URL
from app.core.cache import cache_get, cache_set
from app.core.dates import DateRange, parse_day
from app.core.http import ProviderError, get_json
from app.core.rules import load_rules
from app.core.text import normalize
from app.db.models import EventFestival, utcnow
from app.db.session import SessionLocal
from app.events.store import classify
from app.geo.city import City
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client

logger = logging.getLogger(__name__)

_RANGE_TAIL = re.compile(r"[–—-]\s*(?:[A-Za-z]{3},\s*)?(?P<rest>[A-Za-z]{3,9}\.?\s+\d{1,2}|\d{1,2})\b")


def listing_dates(start_text: str | None, when_text: str | None, anchor: date) -> tuple[date, date] | None:
    """Read a listing's dates ("Oct 22", "Thu, Oct 22, 7 – 10 PM", "Oct 20 – 25", "Sat, Oct 24 – Sun, Oct 25")."""
    start = parse_day(start_text or "", anchor) or parse_day(when_text or "", anchor)
    if start is None:
        return None
    end = start
    tail = _RANGE_TAIL.search(when_text or "")
    if tail:
        rest = tail.group("rest")
        if rest.isdigit():
            try:
                candidate = start.replace(day=int(rest))
            except ValueError:
                candidate = None
        else:
            candidate = parse_day(rest, start)
        if candidate and candidate >= start and (candidate - start).days <= 120:
            end = candidate
    return start, end


def _chip(window: DateRange, today: date) -> str | None:
    """Google Events date filter matching the window, if one does."""
    if window.start == window.end == today:
        return "date:today"
    if window.start == window.end == today + timedelta(days=1):
        return "date:tomorrow"
    if window.kind == "weekend" and window.start - today <= timedelta(days=6):
        return "date:weekend"
    if window.start >= today and window.end <= today + timedelta(days=6):
        return "date:week"
    next_monday = today + timedelta(days=7 - today.weekday())
    if window.start >= next_monday and window.end <= next_monday + timedelta(days=6):
        return "date:next_week"
    if (window.start.year, window.start.month) == (window.end.year, window.end.month) == (today.year, today.month):
        return "date:month"
    following = (today.year + (today.month == 12), today.month % 12 + 1)
    if (window.start.year, window.start.month) == (window.end.year, window.end.month) == following:
        return "date:next_month"
    return None


def _date_phrase(window: DateRange) -> str:
    if window.start == window.end:
        return f"{window.start.strftime('%B')} {window.start.day} {window.start.year}"
    if window.kind == "month":
        return window.start.strftime("%B %Y")
    return f"{window.start.strftime('%B')} {window.start.day} to {window.end.strftime('%B')} {window.end.day} {window.end.year}"


def _record_id(city: City, source: str, title: str, start: date) -> str:
    return "live-" + hashlib.sha1(f"{city.key}|{source}|{normalize(title)}|{start}".encode()).hexdigest()[:16]


def _store(city: City, records: list[dict[str, Any]]) -> int:
    """Upsert live records; skip ones that duplicate a stored record with the same name and overlapping dates."""
    if not records:
        return 0
    stored = 0
    with SessionLocal() as db:
        existing = db.scalars(select(EventFestival).where((EventFestival.city_key == city.key) | (EventFestival.destination_id == city.destination_id) if city.destination_id else (EventFestival.city_key == city.key))).all()
        for record in records:
            duplicate = next((row for row in existing if not row.id.startswith("live-") and normalize(row.title) == normalize(record["title"]) and row.start_date and row.start_date[:10] <= record["end_date"] and (row.end_date or row.start_date)[:10] >= record["start_date"]), None)
            if duplicate:
                continue
            db.merge(EventFestival(updated_at=utcnow(), city_key=city.key, destination_id=city.destination_id, status="active", **record))
            stored += 1
        db.commit()
    return stored


def _serpapi(city: City, window: DateRange, today: date, web: SerpApiClient) -> dict[str, Any]:
    rules = load_rules("events")["live"]
    status = {"source": "Google Events (SerpApi)", "status": "ok", "found": 0, "kept": 0, "undated": 0}
    if not web.configured:
        return {**status, "status": "not_configured"}
    chip = _chip(window, today)
    query = f"events in {city.name}" if chip else rules["query_templates"][0].format(city=city.name, date_phrase=_date_phrase(window))
    try:
        response = web.search(query, engine="google_events", limit=rules["max_results"], extra={"htichips": chip, "gl": (city.country_code or "").lower() or None})
    except SearchProviderError as exc:
        return {**status, "status": "error", "error": exc.as_dict()}
    verified = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for result in response.results:
        status["found"] += 1
        dates = listing_dates(result.event_start, result.event_date, window.start)
        if not dates:
            status["undated"] += 1
            continue
        start, end = dates
        if start > window.end or end < window.start:
            continue
        event_type, category = classify(result.title, result.snippet)
        source_name = f"Google Events listing ({result.source})" if result.url else "Google Events listing"
        records.append({
            "id": _record_id(city, "serpapi", result.title, start), "title": result.title, "summary": result.snippet or None,
            "start_date": start.isoformat(), "end_date": end.isoformat(), "event_type": event_type, "category": category,
            "venue_name": result.venue_name or (result.address.split(",")[0] if result.address else None),
            "source": source_name, "source_url": result.url, "data_source_id": "serpapi_events", "confidence": rules["source_confidence"],
            "source_id": result.place_id, "last_verified_at": verified,
        })
    status["kept"] = _store(city, records)
    return status


def _ticketmaster(city: City, window: DateRange) -> dict[str, Any]:
    status = {"source": "Ticketmaster Discovery API", "status": "ok", "found": 0, "kept": 0, "undated": 0}
    if not TICKETMASTER_API_KEY:
        return {**status, "status": "not_configured"}
    params = {"apikey": TICKETMASTER_API_KEY, "city": city.name, "startDateTime": f"{window.start}T00:00:00Z", "endDateTime": f"{window.end}T23:59:59Z", "size": 30, "sort": "date,asc"}
    if city.country_code:
        params["countryCode"] = city.country_code
    try:
        payload = get_json("ticketmaster", TICKETMASTER_URL, params=params, timeout=8.0)
    except ProviderError as exc:
        return {**status, "status": "error", "error": exc.as_dict()}
    verified = datetime.now(timezone.utc).isoformat(timespec="seconds")
    segment_map = {"music": "music", "sports": "sports", "arts & theatre": "culture", "family": "family", "film": "arts"}
    records = []
    for item in ((payload or {}).get("_embedded") or {}).get("events") or []:
        status["found"] += 1
        dates = item.get("dates") or {}
        start_text = ((dates.get("start") or {}).get("localDate"))
        if not start_text:
            status["undated"] += 1
            continue
        try:
            start = date.fromisoformat(start_text)
            end = date.fromisoformat((dates.get("end") or {}).get("localDate") or start_text)
        except ValueError:
            status["undated"] += 1
            continue
        if start > window.end or end < window.start or ((dates.get("status") or {}).get("code") in {"cancelled", "postponed"}):
            continue
        venue = (((item.get("_embedded") or {}).get("venues")) or [{}])[0]
        location = venue.get("location") or {}
        segment = normalize((((item.get("classifications") or [{}])[0].get("segment")) or {}).get("name"))
        price = (item.get("priceRanges") or [{}])[0]
        event_type, category = classify(item.get("name") or "")
        records.append({
            "id": _record_id(city, "ticketmaster", item.get("name") or "", start), "title": item.get("name") or "Untitled event", "summary": item.get("info") or item.get("pleaseNote"),
            "start_date": start.isoformat(), "end_date": end.isoformat(), "event_type": event_type, "category": segment_map.get(segment, category),
            "venue_name": venue.get("name"), "venue_lat": float(location["latitude"]) if location.get("latitude") else None, "venue_lon": float(location["longitude"]) if location.get("longitude") else None,
            "is_ticketed": True, "ticket_price": f"{float(price['min']):.2f}" if price.get("min") is not None else None, "currency": price.get("currency"),
            "source": "Ticketmaster", "source_url": item.get("url"), "data_source_id": "ticketmaster", "confidence": 0.8, "source_id": item.get("id"), "last_verified_at": verified,
        })
    status["kept"] = _store(city, records)
    return status


def refresh_live_events(city: City, window: DateRange, today: date, web: SerpApiClient | None = None) -> list[dict[str, Any]]:
    """Pull live listings for the window into the store. Returns one status per source."""
    if window.end < today:
        return [{"source": "live listings", "status": "not_applicable", "reason": "Live listings only cover today and later; past dates use stored records."}]
    key = f"{city.key}|{window.start}|{window.end}"
    cached = cache_get("live_events", key)
    if cached is not None:
        return [{**item, "cached": True} for item in cached["data"]]
    statuses = [_ticketmaster(city, window), _serpapi(city, window, today, web or default_client)]
    if not any(s["status"] == "error" for s in statuses):
        cache_set("live_events", key, statuses, load_rules("events")["live"]["cache_ttl_s"], source="events")
    logger.info("live_events city=%s window=%s..%s statuses=%s", city.key, window.start, window.end, [(s["source"], s["status"], s.get("kept")) for s in statuses])
    return statuses
