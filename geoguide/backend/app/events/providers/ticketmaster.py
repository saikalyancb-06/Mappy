"""Ticketmaster Discovery API (server-side only; the key never reaches the frontend).

Uses the API's own filters — lat/long + radius, UTC date range, keyword, classification —
and pages through results instead of downloading large datasets.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import TICKETMASTER_API_KEY, TICKETMASTER_URL
from app.core.cache import cache_get, cache_set
from app.core.http import ProviderError, get_json
from app.core.rules import load_rules
from app.events.model import EventQuery, EventSource, NormalisedEvent, zone
from app.events.providers.base import EventProvider, ProviderResult
from app.events.taxonomy import categorise

PAGE_SIZE = 50
MAX_PAGES = 3
CANCELLED = {"cancelled", "canceled", "postponed", "offsale"}


def _money(value: Any) -> str | None:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01"))) if value is not None else None
    except InvalidOperation:
        return None


class TicketmasterProvider(EventProvider):
    name, label = "ticketmaster", "Ticketmaster Discovery API"

    def __init__(self, api_key: str | None = None, url: str | None = None) -> None:
        self.api_key = TICKETMASTER_API_KEY if api_key is None else api_key
        self.url = url or TICKETMASTER_URL

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.configured, "countries": self.coverage() or "all", "note": None if self.configured else "Set TICKETMASTER_API_KEY on the server to enable."}

    def _params(self, query: EventQuery, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "apikey": self.api_key, "latlong": f"{query.centre[0]:.5f},{query.centre[1]:.5f}", "radius": max(1, int(round(query.radius_km))), "unit": "km",
            "startDateTime": query.start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": query.end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": PAGE_SIZE, "page": page, "sort": "date,asc",
        }
        if query.text:
            params["keyword"] = query.text
        classes = [load_rules("events")["ticketmaster_classifications"].get(c) for c in query.categories]
        if any(classes):
            params["classificationName"] = ",".join(dict.fromkeys(c for c in classes if c))
        return params

    def search_events(self, query: EventQuery) -> ProviderResult:
        started = self._timed()
        result = ProviderResult(self.name, self.label)
        if not self.configured:
            result.status = "not_configured"
            return result
        cache_key = query.cache_key(self.name)
        cached = cache_get("events_ticketmaster", cache_key)
        items: list[dict[str, Any]] = []
        retrieved = datetime.now(timezone.utc)
        if cached is not None:
            items, result.cached = cached["data"]["items"], True
            retrieved = datetime.fromisoformat(cached["data"]["retrieved_at"])
        else:
            try:
                for page in range(MAX_PAGES):
                    payload = get_json("ticketmaster", self.url, params=self._params(query, page), timeout=8.0) or {}
                    items.extend(((payload.get("_embedded") or {}).get("events")) or [])
                    total_pages = (payload.get("page") or {}).get("totalPages") or 1
                    if page + 1 >= total_pages:
                        break
            except ProviderError as exc:
                result.status, result.error = "error", exc.as_dict()
                result.latency_ms = int((self._timed() - started) * 1000)
                return result
            cache_set("events_ticketmaster", cache_key, {"items": items, "retrieved_at": retrieved.isoformat()}, load_rules("events")["cache_ttl_s"]["ticketmaster"], source="ticketmaster")
        result.raw_count = len(items)
        for item in items:
            event = self.normalise(item, query, retrieved, result)
            if event:
                result.events.append(event)
        result.latency_ms = int((self._timed() - started) * 1000)
        return result

    def get_event(self, event_id: str, query: EventQuery | None = None) -> NormalisedEvent | None:
        if not self.configured or query is None:
            return None
        try:
            item = get_json("ticketmaster", self.url.replace("events.json", f"events/{event_id}.json"), params={"apikey": self.api_key}, timeout=8.0)
        except ProviderError:
            return None
        return self.normalise(item or {}, query, datetime.now(timezone.utc), ProviderResult(self.name, self.label))

    def normalise(self, item: dict[str, Any], query: EventQuery, retrieved: datetime, result: ProviderResult) -> NormalisedEvent | None:
        dates = item.get("dates") or {}
        if ((dates.get("status") or {}).get("code") or "").lower() in CANCELLED:
            result.drop("cancelled")
            return None
        start_info = dates.get("start") or {}
        tz = zone(dates.get("timezone") or query.city.timezone)
        start: datetime | None = None
        time_known = False
        if start_info.get("dateTime"):
            start = datetime.fromisoformat(start_info["dateTime"].replace("Z", "+00:00")).astimezone(tz)
            time_known = not start_info.get("noSpecificTime")
        elif start_info.get("localDate"):
            try:
                local_time = start_info.get("localTime") or "00:00:00"
                start = datetime.fromisoformat(f"{start_info['localDate']}T{local_time}").replace(tzinfo=tz)
                time_known = bool(start_info.get("localTime"))
            except ValueError:
                start = None
        if start is None:
            result.drop("no_date")
            return None
        end = None
        end_info = dates.get("end") or {}
        if end_info.get("dateTime"):
            end = datetime.fromisoformat(end_info["dateTime"].replace("Z", "+00:00")).astimezone(tz)
        venue = (((item.get("_embedded") or {}).get("venues")) or [{}])[0]
        location = venue.get("location") or {}
        classification = (item.get("classifications") or [{}])[0]
        hints = [((classification.get(level) or {}).get("name") or "") for level in ("segment", "genre", "subGenre")]
        category, categories, kind = categorise(item.get("name") or "", item.get("info") or item.get("description"), self.name, [h for h in hints if h])
        prices = item.get("priceRanges") or []
        images = sorted((i for i in item.get("images") or [] if i.get("url")), key=lambda i: -(i.get("width") or 0))
        source = EventSource(provider=self.name, name="Ticketmaster", kind="ticketmaster", reliability=load_rules("events")["sources"]["ticketmaster"], source_event_id=item.get("id"), url=item.get("url"), retrieved_at=retrieved)
        return NormalisedEvent(
            title=(item.get("name") or "").strip() or "Untitled event", start=start.astimezone(query.tz), end=end.astimezone(query.tz) if end else None, timezone=str(query.tz),
            time_known=time_known, source=source, description=item.get("info") or item.get("description") or item.get("pleaseNote"), category=category, categories=categories, type=kind,
            venue_name=venue.get("name"), venue_address=((venue.get("address") or {}).get("line1")), lat=float(location["latitude"]) if location.get("latitude") else None,
            lon=float(location["longitude"]) if location.get("longitude") else None, city=((venue.get("city") or {}).get("name")) or query.city.name,
            event_url=item.get("url"), ticket_url=item.get("url"), image_url=images[0]["url"] if images else None,
            price_kind="paid" if prices else "unknown", price_min=_money(prices[0].get("min")) if prices else None, price_max=_money(prices[0].get("max")) if prices else None,
            currency=prices[0].get("currency") if prices else None, organizer=((item.get("promoter") or {}).get("name")),
        )
