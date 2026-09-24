"""Web-search fallback for the long tail of local events (organic results via SerpApi).

Several complementary queries are generated from the destination, dates, season and the
kind of event asked for. A result becomes an event only with evidence: a single event
(not a round-up page), an explicit date inside the range, and the destination named or a
venue given. Source reliability depends on the kind of site (official > platform > news >
aggregator). Nothing is invented: unreadable dates drop the result.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.context.season import season_for
from app.core.rules import load_rules
from app.core.text import normalize
from app.events.extract import clean_title, combine, listing_dates, listing_times, venue_from_text
from app.events.model import EventQuery, EventSource, NormalisedEvent
from app.events.providers.base import EventProvider, ProviderResult
from app.events.sources import domain, is_listicle, web_source_kind
from app.events.taxonomy import categorise, price_from_text
from app.search.serpapi import SearchProviderError, SearchResult, SerpApiClient, client as default_client


def build_queries(query: EventQuery) -> list[str]:
    """Complementary searches for the request; never a fixed list of phrases."""
    rules = load_rules("events")
    templates = rules["web_queries"]
    window = query.window
    when = {"tonight": "tonight", "weekend": "this weekend"}.get(window.kind) or (window.start.strftime("%B %d %Y") if window.start == window.end else f"{window.start.strftime('%B %d')} to {window.end.strftime('%B %d %Y')}")
    month_year = window.start.strftime("%B %Y")
    city = query.city.name
    queries: list[str] = []
    if query.text:
        queries.append(f"{query.text} {city} {when}")
    for category in query.categories[:2]:
        queries.append(templates["category"].format(category=rules["categories"][category]["label"].lower(), city=city, when=when))
    if query.festival_only or "festivals" in query.categories or not query.categories:
        queries.extend(t.format(city=city, month_year=month_year) for t in templates["festival"][:1])
    queries.extend(t.format(city=city, when=when) for t in templates["generic"])
    season = season_for(query.city, window.start).get("name")
    if season:
        queries.append(templates["season"].format(season=season, city=city, month_year=month_year))
    return list(dict.fromkeys(" ".join(q.split()) for q in queries))[: templates["max_queries"]]


class WebEventProvider(EventProvider):
    name, label = "web", "Web search (event pages)"

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
        rules = load_rules("events")["web_queries"]
        seen_urls: set[str] = set()
        errors = []
        for text in build_queries(query):
            result.queries.append(text)
            try:
                response = self.web.search(text, engine="google", limit=rules["results_per_query"])
            except SearchProviderError as exc:
                errors.append(exc.as_dict())
                continue
            retrieved = datetime.fromisoformat(response.retrieved_at.replace("Z", "+00:00")) if response.retrieved_at else datetime.now(timezone.utc)
            result.cached = result.cached or response.cached
            for item in response.results:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url or "")
                result.raw_count += 1
                event = self.extract(item, query, retrieved, result)
                if event:
                    result.events.append(event)
        if errors and not result.raw_count:
            result.status, result.error = "error", errors[0]
        result.latency_ms = int((self._timed() - started) * 1000)
        return result

    def extract(self, item: SearchResult, query: EventQuery, retrieved: datetime, result: ProviderResult) -> NormalisedEvent | None:
        text = f"{item.title}. {item.snippet or ''}"
        if is_listicle(item.title):
            result.drop("not_an_event")  # round-ups are not single events
            return None
        dates = listing_dates(None, item.title, query.window.start) or listing_dates(None, item.snippet, query.window.start)
        if not dates:
            result.drop("no_date")
            return None
        city_norm = normalize(query.city.name)
        venue = venue_from_text(item.snippet) or venue_from_text(item.title)
        if f" {city_norm} " not in f" {normalize(text + ' ' + (item.url or ''))} " and not venue:
            result.drop("location_unclear")
            return None
        start_time, end_time = listing_times(item.snippet)
        kind, reliability = web_source_kind(item.url)
        category, categories, event_type = categorise(clean_title(item.title), item.snippet)
        price_kind, price_min, currency = price_from_text(item.snippet)
        host = domain(item.url)
        source = EventSource(provider=self.name, name=host or "web page", kind=kind, reliability=reliability, source_event_id=item.url, url=item.url, retrieved_at=retrieved, last_updated=item.published_date)
        return NormalisedEvent(
            title=clean_title(item.title), start=combine(dates[0], start_time, query.tz), end=combine(dates[1], end_time, query.tz) if (end_time or dates[1] != dates[0]) else None,
            timezone=str(query.tz), time_known=start_time is not None, source=source, description=item.snippet or None, category=category, categories=categories, type=event_type,
            venue_name=venue, city=query.city.name, event_url=item.url, price_kind=price_kind, price_min=price_min, currency=currency,
        )
