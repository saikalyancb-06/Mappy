"""Google Events listings via SerpApi (server-side key).

Listings are kept only when their date can be read and overlaps the requested range.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.rules import load_rules
from app.events.extract import clean_title, combine, listing_dates, listing_times, venue_from_text
from app.events.model import EventQuery, EventSource, NormalisedEvent
from app.events.providers.base import EventProvider, ProviderResult
from app.events.sources import domain
from app.events.taxonomy import categorise, price_from_text
from app.search.serpapi import SearchProviderError, SearchResult, SerpApiClient, client as default_client


def _chip(query: EventQuery) -> str | None:
    """Google Events' own date filter when one matches the requested range."""
    today = datetime.now(query.tz).date()
    start, end, kind = query.window.start, query.window.end, query.window.kind
    if start == end == today:
        return "date:today"
    if start == end == today + timedelta(days=1):
        return "date:tomorrow"
    if kind == "weekend" and start - today <= timedelta(days=6):
        return "date:weekend"
    if start >= today and end <= today + timedelta(days=6):
        return "date:week"
    monday = today + timedelta(days=7 - today.weekday())
    if start >= monday and end <= monday + timedelta(days=6):
        return "date:next_week"
    if (start.year, start.month) == (end.year, end.month) == (today.year, today.month):
        return "date:month"
    return None


class GoogleEventsProvider(EventProvider):
    name, label = "google_events", "Google Events (via SerpApi)"

    def __init__(self, web: SerpApiClient | None = None) -> None:
        self.web = web or default_client

    @property
    def configured(self) -> bool:
        return self.web.configured

    def search_events(self, query: EventQuery) -> ProviderResult:
        started = self._timed()
        result = ProviderResult(self.name, self.label)
        if not self.configured:
            result.status = "not_configured"
            return result
        chip = _chip(query)
        topic = " ".join(load_rules("events")["categories"][c]["label"].lower() for c in query.categories[:2]) or "events"
        text = f"{query.text} in {query.city.name}" if query.text else f"{topic} in {query.city.name}"
        if not chip:
            text += f" {query.window.start.strftime('%B %Y')}" if query.window.start.month == query.window.end.month else f" {query.window.start.strftime('%B %d')} to {query.window.end.strftime('%B %d %Y')}"
        result.queries.append(text)
        try:
            response = self.web.search(text, engine="google_events", limit=20, extra={"htichips": chip, "gl": (query.city.country_code or "").lower() or None})
        except SearchProviderError as exc:
            result.status, result.error = "error", exc.as_dict()
            result.latency_ms = int((self._timed() - started) * 1000)
            return result
        result.cached = response.cached
        retrieved = datetime.fromisoformat(response.retrieved_at.replace("Z", "+00:00")) if response.retrieved_at else datetime.now(timezone.utc)
        result.raw_count = len(response.results)
        for item in response.results:
            event = self.normalise(item, query, retrieved, result)
            if event:
                result.events.append(event)
        result.latency_ms = int((self._timed() - started) * 1000)
        return result

    def normalise(self, item: SearchResult, query: EventQuery, retrieved: datetime, result: ProviderResult) -> NormalisedEvent | None:
        dates = listing_dates(item.event_start, item.event_date, query.window.start)
        if not dates:
            result.drop("no_date")
            return None
        start_time, end_time = listing_times(item.event_date)
        start = combine(dates[0], start_time, query.tz)
        end = combine(dates[1], end_time, query.tz) if (end_time or dates[1] != dates[0]) else None
        if end and end < start:
            end = None
        category, categories, kind = categorise(item.title, item.snippet)
        price_kind, price_min, currency = price_from_text(item.snippet)
        host = domain(item.url)
        source = EventSource(provider=self.name, name=f"Google Events listing ({host})" if host else "Google Events listing", kind="google_events",
                             reliability=load_rules("events")["sources"]["google_events"], source_event_id=item.place_id or item.url, url=item.url, retrieved_at=retrieved)
        return NormalisedEvent(
            title=clean_title(item.title), start=start, end=end, timezone=str(query.tz), time_known=start_time is not None, source=source,
            description=item.snippet or None, category=category, categories=categories, type=kind,
            venue_name=item.venue_name or (item.address.split(",")[0].strip() if item.address else None) or venue_from_text(item.snippet),
            venue_address=item.address, city=query.city.name, event_url=item.url, ticket_url=item.ticket_url, image_url=item.image_url,
            price_kind=price_kind, price_min=price_min, currency=currency,
        )
