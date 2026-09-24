"""De-duplication, freshness and confidence for normalised events."""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from app.core.rules import load_rules
from app.core.text import name_similarity, normalize
from app.events.model import NormalisedEvent
from app.events.sources import domain
from app.geo.distance import haversine_km

_NOISE = re.compile(r"\b(?:20\d\d|tickets?|live|presents?|official|edition|the|annual)\b")


def _title_key(title: str, city: str | None) -> str:
    text = normalize(re.split(r"\s+(?:at|@)\s+", title, maxsplit=1)[0])
    if city:
        text = text.replace(normalize(city), " ")
    return " ".join(_NOISE.sub(" ", text).split())


def same_event(a: NormalisedEvent, b: NormalisedEvent) -> bool:
    """Same underlying event: matching ids/URLs, or close titles on the same dates at a compatible venue.
    Similar names alone are never enough."""
    if a.source.provider == b.source.provider and a.source.source_event_id and a.source.source_event_id == b.source.source_event_id:
        return True
    same_day = a.start.date() == b.start.date()
    overlapping = a.start.date() <= b.effective_end.date() and b.start.date() <= a.effective_end.date()
    if not (same_day or (overlapping and (a.effective_end.date() > a.start.date() or b.effective_end.date() > b.start.date()))):
        return False
    if a.event_url and a.event_url == b.event_url:
        return True
    ka, kb = _title_key(a.title, a.city), _title_key(b.title, b.city)
    if not ka or not kb:
        return False
    close = SequenceMatcher(None, ka, kb).ratio() >= 0.82 or (len(ka.split()) >= 2 and len(kb.split()) >= 2 and (ka in kb or kb in ka))
    if not close:
        return False
    if a.lat is not None and b.lat is not None and a.lon is not None and b.lon is not None:
        return haversine_km(a.lat, a.lon, b.lat, b.lon) <= 1.0
    if a.venue_name and b.venue_name:
        return name_similarity(a.venue_name, b.venue_name) >= 0.6 or normalize(a.venue_name) in normalize(b.venue_name) or normalize(b.venue_name) in normalize(a.venue_name)
    if a.time_known and b.time_known and abs((a.start - b.start).total_seconds()) > 2 * 3600:
        return False
    return True


def _completeness(event: NormalisedEvent) -> int:
    return sum(bool(x) for x in (event.time_known, event.venue_name, event.lat, event.event_url, event.description, event.image_url, event.price_kind != "unknown"))


def merge(group: list[NormalisedEvent]) -> NormalisedEvent:
    group = sorted(group, key=lambda e: (-e.source.reliability, -_completeness(e)))
    primary = group[0]
    for other in group[1:]:
        for attr in ("description", "venue_name", "venue_address", "lat", "lon", "event_url", "ticket_url", "image_url", "organizer", "end", "significance", "traditions", "etiquette", "expected_footfall", "locality"):
            if getattr(primary, attr) in (None, "") and getattr(other, attr) not in (None, ""):
                setattr(primary, attr, getattr(other, attr))
        if not primary.time_known and other.time_known and other.start.date() == primary.start.date():
            primary.start, primary.time_known = other.start, True
        if primary.price_kind == "unknown" and other.price_kind != "unknown":
            primary.price_kind, primary.price_min, primary.price_max, primary.currency = other.price_kind, other.price_min, other.price_max, other.currency
        for category in other.categories:
            if category not in primary.categories:
                primary.categories.append(category)
        if other.type == "festival":
            primary.type = "festival"
    primary.sources = sorted({(s.provider, s.url or s.source_event_id or s.name): s for e in group for s in e.sources}.values(), key=lambda s: -s.reliability)
    return primary


def deduplicate(events: list[NormalisedEvent]) -> tuple[list[NormalisedEvent], int]:
    groups: list[list[NormalisedEvent]] = []
    for event in events:
        for group in groups:
            if any(same_event(event, member) for member in group):
                group.append(event)
                break
        else:
            groups.append([event])
    return [merge(group) for group in groups], len(events) - len(groups)


def freshness(event: NormalisedEvent, now: datetime) -> str:
    """fresh / recent / stale for live listings, stored for dataset records, expired once the event is over."""
    if event.effective_end < now:
        return "expired"
    rules = load_rules("events")["freshness"]
    retrieved = max((s.retrieved_at for s in event.sources if s.retrieved_at), default=None)
    if retrieved is None:
        return "stored"
    age_min = (now - retrieved).total_seconds() / 60
    return "fresh" if age_min <= rules["fresh_min"] else "recent" if age_min <= rules["recent_min"] else "stale"


def assess(event: NormalisedEvent) -> None:
    """Confidence 0..1 from evidence: source reliability, explicit date/time, venue, coordinates,
    corroboration by independent sources, and freshness."""
    rules = load_rules("events")["ranking"]
    reliability = max(s.reliability for s in event.sources)
    independent = len({(s.provider, domain(s.url) or s.name) for s in event.sources})
    parts = {
        "source": reliability,
        "date": 1.0 if event.time_known else 0.7,
        "venue": 1.0 if event.venue_name else 0.0,
        "coordinates": 1.0 if event.lat is not None else 0.0,
        "corroboration": min(1.0, (independent - 1) / 2),
        "freshness": {"fresh": 1.0, "recent": 0.8, "stored": 0.7, "stale": 0.35}.get(event.freshness, 0.5),
    }
    value = 0.45 * parts["source"] + 0.15 * parts["date"] + 0.1 * parts["venue"] + 0.1 * parts["coordinates"] + 0.1 * parts["corroboration"] + 0.1 * parts["freshness"]
    notes = []
    kind = event.sources[0].kind
    notes.append({"ticketmaster": "Ticketing platform listing", "web_authoritative": "Official source", "web_platform": "Established event platform", "stored_curated": "Curated record", "stored_dataset": "Organiser dataset (synthetic)", "google_events": "Event listing", "web_news": "News report", "web_aggregator": "Aggregator listing — verify", "web_unknown": "Unverified web page — verify"}.get(kind, kind))
    if independent > 1:
        notes.append(f"Reported by {independent} sources")
    if not event.time_known:
        notes.append("Date only — check the time")
    if not event.venue_name:
        notes.append("No venue given")
    if event.freshness == "stale":
        notes.append("Listing not re-checked recently")
    event.confidence = round(value, 3)
    event.confidence_label = "high" if value >= rules["high_confidence_at"] else "medium" if value >= rules["low_confidence_below"] else "low"
    event.confidence_notes = notes
