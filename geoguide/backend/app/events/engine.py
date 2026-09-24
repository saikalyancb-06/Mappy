"""Event discovery pipeline.

    intent → destination → date/time range → structured providers → web fallback (when needed)
    → normalise → validate dates/place → link venues → freshness (drop expired) → de-duplicate
    → confidence → filters → rank (time, distance, relevance, confidence, freshness, vibe, popularity)

Events never come from the RAG store or the LLM. Provider failures are isolated: the others
still answer, and a failure is reported, never papered over with invented events.
"""
from __future__ import annotations

import logging
import math
import time as clock
from datetime import date, datetime, time, timedelta
from typing import Any

from app.core.dates import DateRange
from app.core.rules import load_rules
from app.core.text import normalize, tokens
from app.events.model import EventQuery, NormalisedEvent, day_bounds, zone
from app.events.providers.base import EventProvider, ProviderResult
from app.events.providers.google_events import GoogleEventsProvider
from app.events.providers.stored import StoredEventProvider
from app.events.providers.ticketmaster import TicketmasterProvider
from app.events.providers.web import WebEventProvider
from app.events.quality import assess, deduplicate, freshness
from app.events.venues import enrich_venues
from app.feedback.signals import user_vibes
from app.geo.city import City, country_code
from app.geo.distance import haversine_km
from app.geo.geo_context import destination_by_id
from app.search.serpapi import SerpApiClient

logger = logging.getLogger(__name__)
WEB_FALLBACK_BELOW = 5  # use the long-tail web search when structured sources return fewer events than this


def build_query(city: City, window: DateRange, *, mode: str = "destination", user_point: tuple[float, float] | None = None, radius_km: float | None = None,
                text: str | None = None, categories: list[str] | None = None, free_only: bool = False, festival_only: bool = False,
                user: dict[str, Any] | None = None, limit: int = 40, include_live: bool = True) -> EventQuery:
    """Turn a destination (or "near me") and a date range into an explicit, timezone-aware query."""
    rules = load_rules("events")
    tz = zone(city.timezone)
    start, _ = day_bounds(window.start, tz)
    _, end = day_bounds(window.end, tz)
    if window.kind == "tonight":
        hour, minute = map(int, rules["time_windows"]["tonight_from"].split(":"))
        start = datetime.combine(window.start, time(hour, minute), tzinfo=tz)
    now = datetime.now(tz)
    if window.start <= now.date() <= window.end and start < now:
        start = now.replace(second=0, microsecond=0)  # what's already over isn't "happening"
    geo = rules["geo"]
    if mode == "near_me" and user_point:
        centre, radius = user_point, min(radius_km or geo["near_me_radius_km"], geo["max_radius_km"])
    else:
        destination = destination_by_id(city.destination_id)
        coverage = float(destination.coverage_radius_km) if destination and destination.coverage_radius_km else 0.0
        centre, radius = (city.lat, city.lon), min(radius_km or max(geo["destination_min_radius_km"], coverage + geo["destination_radius_margin_km"]), geo["max_radius_km"])
        mode = "destination"
    known = set(rules["categories"])
    return EventQuery(city=city, window=window, start=start, end=end, centre=centre, radius_km=radius, mode=mode, text=(text or None), categories=[c for c in (categories or []) if c in known],
                      free_only=free_only, festival_only=festival_only, user=user or {}, user_point=user_point, limit=limit, include_live=include_live)


def default_providers(web: SerpApiClient | None = None) -> list[EventProvider]:
    return [StoredEventProvider(), TicketmasterProvider(), GoogleEventsProvider(web), WebEventProvider(web)]


def _time_score(event: NormalisedEvent, query: EventQuery, now: datetime) -> float:
    if event.start <= now <= event.effective_end:
        return 1.0  # on right now
    span = max((query.end - query.start).total_seconds(), 3600.0)
    offset = max(0.0, (event.start - query.start).total_seconds())
    return max(0.2, 1.0 - 0.8 * offset / span)


def _relevance(event: NormalisedEvent, query: EventQuery) -> float:
    score = 0.5
    if query.categories:
        score = 1.0 if set(event.categories) & set(query.categories) else 0.2
    if query.text:
        wanted = set(tokens(query.text))
        have = set(tokens(f"{event.title} {event.description or ''} {' '.join(event.categories)}"))
        if wanted:
            score = 0.5 * score + 0.5 * (len(wanted & have) / len(wanted))
    if query.festival_only and event.type == "festival":
        score = min(1.0, score + 0.2)
    return score


def _vibe(event: NormalisedEvent, prefs: Any) -> tuple[float, list[str]]:
    rules = load_rules("events")["categories"]
    vibes = {v for c in event.categories for v in rules.get(c, {}).get("vibes", [])}
    centred = {k: a - 0.5 for k, a in prefs.affinity.items() if abs(a - 0.5) > 0.01}
    if not vibes or not centred:
        return 0.5, []
    strength = max(prefs.confidence, 0.15)
    match = sum(centred.get(v, 0.0) for v in vibes) / len(vibes) * 2
    liked = [v for v in vibes if centred.get(v, 0) > 0.1]
    reasons = [f"Matches vibes you enjoy: {', '.join(sorted(liked)[:2]).replace('_', ' ')}"] if match * strength > 0.15 and liked else []
    return max(0.0, min(1.0, 0.5 + 0.5 * match * strength)), reasons


def rank_events(events: list[NormalisedEvent], query: EventQuery, now: datetime) -> list[NormalisedEvent]:
    rules = load_rules("events")["ranking"]
    weights = rules["weights"]
    prefs = user_vibes(query.user.get("user_id"), query.user)
    for event in events:
        vibe, vibe_reasons = _vibe(event, prefs)
        parts = {
            "time": _time_score(event, query, now),
            "distance": math.exp(-event.distance_km / rules["distance_scale_km"]) if event.distance_km is not None else 0.5,
            "relevance": _relevance(event, query),
            "confidence": event.confidence,
            "freshness": {"fresh": 1.0, "recent": 0.8, "stored": 0.7, "stale": 0.3}.get(event.freshness, 0.5),
            "vibe": vibe,
            "popularity": min(1.0, event.expected_footfall / rules["footfall_scale"]) if event.expected_footfall else 0.5,
        }
        score = sum(weights[k] * v for k, v in parts.items())
        if event.confidence_label == "low":
            score *= 0.8  # low-confidence events are never presented as equally reliable
        event.score, event.score_parts = round(score, 4), {k: round(v, 3) for k, v in parts.items()}
        reasons = []
        if event.start <= now <= event.effective_end:
            reasons.append("On now")
        elif event.time_known and not event.all_day:
            reasons.append(f"{event.start.strftime('%a %d %b, %H:%M')}")
        if event.distance_km is not None:
            reasons.append(f"{event.distance_km:.1f} km {'from you' if query.user_point and query.mode == 'near_me' else 'from ' + query.city.name + ' centre'}")
        if event.price_kind == "free":
            reasons.append("Free")
        reasons += vibe_reasons + event.confidence_notes[:2]
        event.reasons = reasons
    return sorted(events, key=lambda e: (-e.score, e.start))


def _wanted(event: NormalisedEvent, query: EventQuery) -> bool:
    """Whether an event answers the request (dates and filters), before de-duplication."""
    if not event.overlaps(query.start, query.end):
        return False
    if query.categories and not set(event.categories) & set(query.categories):
        return False
    if query.free_only and event.price_kind != "free":
        return False
    return not query.festival_only or event.type == "festival" or bool(set(event.categories) & set(load_rules("events")["festival_categories"]))


def find_events(query: EventQuery, providers: list[EventProvider] | None = None, now: datetime | None = None, web: SerpApiClient | None = None) -> dict[str, Any]:
    started = clock.monotonic()
    now = now or datetime.now(query.tz)
    providers = providers if providers is not None else default_providers(web)
    results: list[ProviderResult] = []

    def run(provider: EventProvider) -> None:
        if not provider.covers(query):
            note = load_rules("events").get("provider_coverage", {}).get(provider.name, {}).get("skip_note", "{country}: not covered; skipped.")
            results.append(ProviderResult(provider.name, provider.label, status="not_applicable", error={"source": provider.name, "code": "no_coverage", "message": note.format(country=query.city.country or country_code(query.city) or "this country")}))
            return
        if provider.name != "stored" and query.end < now:
            results.append(ProviderResult(provider.name, provider.label, status="not_applicable", error={"source": provider.name, "code": "past_dates", "message": "Live listings only cover today onwards; past dates use stored records."}))
            return
        try:
            results.append(provider.search_events(query))
        except Exception as exc:  # one provider failing must not take the others down
            logger.warning("event_provider_failed provider=%s error=%s", provider.name, type(exc).__name__)
            results.append(ProviderResult(provider.name, provider.label, status="error", error={"source": provider.name, "code": "provider_failure", "message": "The provider failed unexpectedly."}))

    structured = [p for p in providers if p.name != "web" and (query.include_live or p.name == "stored")]
    for provider in structured:
        run(provider)
    fallback = [p for p in providers if p.name == "web"]
    matching = sum(1 for r in results for e in r.events if _wanted(e, query))
    if query.include_live and fallback and (matching < WEB_FALLBACK_BELOW or query.festival_only):
        run(fallback[0])

    counts = {"raw": sum(r.raw_count for r in results), "normalised": 0, "out_of_range": 0, "outside_area": 0, "expired": 0, "duplicates": 0, "filtered": 0, "returned": 0}
    events: list[NormalisedEvent] = []
    for result in results:
        for event in result.events:
            counts["normalised"] += 1
            if not event.overlaps(query.start, query.end):
                counts["out_of_range"] += 1
                continue
            if query.window.kind == "tonight" and event.time_known and event.start.date() == query.window.start and event.effective_end < query.start:
                counts["out_of_range"] += 1
                continue
            events.append(event)
    enrich_venues(events, query.city)
    kept = []
    for event in events:
        if event.lat is not None and event.lon is not None:
            distance = haversine_km(query.centre[0], query.centre[1], event.lat, event.lon)
            # Stored records are linked to their city; live/web results must fall inside the search boundary.
            if distance > query.radius_km and (event.source.provider != "stored" or query.mode == "near_me"):
                counts["outside_area"] += 1
                continue
            point = query.user_point if query.user_point else query.centre
            event.distance_km = round(haversine_km(point[0], point[1], event.lat, event.lon), 2)
        event.freshness = freshness(event, now)
        if event.freshness == "expired":
            counts["expired"] += 1
            continue
        kept.append(event)
    events, counts["duplicates"] = deduplicate(kept)
    for event in events:
        event.freshness = freshness(event, now)
        assess(event)
    before = len(events)
    if query.categories:
        events = [e for e in events if set(e.categories) & set(query.categories)]
    if query.free_only:
        events = [e for e in events if e.price_kind == "free"]
    if query.festival_only:
        events = [e for e in events if e.type == "festival" or set(e.categories) & set(load_rules("events")["festival_categories"])]
    counts["filtered"] = before - len(events)
    events = rank_events(events, query, now)[: query.limit]
    counts["returned"] = len(events)
    logger.info("events_search city=%s mode=%s window=%s..%s radius_km=%s providers=%s counts=%s latency_ms=%d",
                query.city.key, query.mode, query.start.isoformat(), query.end.isoformat(), query.radius_km,
                [(r.provider, r.status, r.raw_count, len(r.events), r.latency_ms, r.dropped) for r in results], counts, int((clock.monotonic() - started) * 1000))
    return {"events": events, "providers": results, "counts": counts}


def upcoming_window(anchor: date) -> DateRange:
    from app.core.dates import fmt_range

    end = anchor + timedelta(days=load_rules("events")["upcoming_days"] - 1)
    return DateRange(anchor, end, fmt_range(anchor, end), "upcoming")


def normalize_query_text(text: str | None) -> str | None:
    cleaned = normalize(text or "")
    return cleaned or None
