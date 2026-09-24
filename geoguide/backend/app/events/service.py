"""What's happening: city-scoped, date-native event answers for Explore, Ask and the events API.

Hard rule: the result is exactly the verified events that match the place and the dates.
When there are none, ``events`` is empty and ``event_status`` is ``no_verified_events_found``;
nothing is substituted, not even a festival the city is known for (those appear only under
``associated_festivals``, labelled as unconfirmed for these dates).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from app.core.dates import DateRange, single, weekend_of
from app.core.rules import load_rules
from app.events.engine import build_query, find_events, upcoming_window
from app.events.model import zone
from app.events.providers.base import EventProvider
from app.events.store import associated_festivals
from app.geo.city import City
from app.search.serpapi import SerpApiClient

FOUND = "verified_events_found"
NONE = "no_verified_events_found"


def no_events_message(city: City, window: DateRange) -> str:
    when = f"on {window.label}" if window.start == window.end and window.kind != "tonight" else f"for {window.label}"
    return f"No verified events found for {city.name} {when} in the available sources."


def _groups(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = load_rules("events")
    order = {c: i for i, c in enumerate(rules["category_priority"])}
    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(event["category"], []).append(event["id"])
    return [{"id": cid, "label": rules["categories"].get(cid, rules["categories"]["other"])["label"], "event_ids": ids} for cid, ids in sorted(grouped.items(), key=lambda kv: order.get(kv[0], 99))]


def city_events(city: City, window: DateRange, *, today: date | None = None, user_point: tuple[float, float] | None = None, live: bool = True,
                web: SerpApiClient | None = None, profile: dict[str, Any] | None = None, text: str | None = None, categories: list[str] | None = None,
                free_only: bool = False, festival_only: bool = False, mode: str = "destination", radius_km: float | None = None,
                providers: list[EventProvider] | None = None, limit: int = 40) -> dict[str, Any]:
    query = build_query(city, window, mode=mode, user_point=user_point, radius_km=radius_km, text=text, categories=categories, free_only=free_only,
                        festival_only=festival_only, user=profile, limit=limit, include_live=live)
    found = find_events(query, providers=providers, web=web)
    events = [e.as_dict(window) for e in found["events"]]
    return {
        "city": city.as_dict(),
        "range": {**window.as_dict(), "start_time": query.start.isoformat(), "end_time": query.end.isoformat()},
        "area": {"mode": query.mode, "centre": {"lat": query.centre[0], "lon": query.centre[1]}, "radius_km": query.radius_km},
        "event_status": FOUND if events else NONE,
        "message": None if events else no_events_message(city, window),
        "events": events,
        "festivals": [e for e in events if e["type"] == "festival"],
        "live_events": [e for e in events if e["type"] != "festival"],
        "groups": _groups(events),
        "associated_festivals": associated_festivals(city, window),
        "sources_checked": [r.as_dict() for r in found["providers"]],
        "counts": found["counts"],
    }


def events_overview(city: City, anchor: date, *, user_point: tuple[float, float] | None = None, profile: dict[str, Any] | None = None, web: SerpApiClient | None = None,
                    mode: str = "destination", radius_km: float | None = None, providers: list[EventProvider] | None = None) -> dict[str, Any]:
    """One retrieval for the next weeks, split into the tabs that actually have events (Today, Tonight, This weekend, Festivals…)."""
    window = upcoming_window(anchor)
    result = city_events(city, window, user_point=user_point, web=web, profile=profile, mode=mode, radius_km=radius_km, providers=providers, limit=120)
    tz = zone(city.timezone)
    rules = load_rules("events")
    weekend = weekend_of(anchor)
    evening = datetime.combine(anchor, time.fromisoformat(rules["time_windows"]["evening_from"]), tzinfo=tz)

    def when(event: dict[str, Any], tab: str) -> bool:
        start_day, end_day = date.fromisoformat(event["start_date"]), date.fromisoformat(event["end_date"])
        if tab == "today":
            return start_day <= anchor <= end_day
        if tab == "tonight":
            if not (start_day <= anchor <= end_day):
                return False
            return event["all_day"] or datetime.fromisoformat(event["end"] or event["start"]) >= evening or datetime.fromisoformat(event["start"]) >= evening
        if tab == "weekend":
            return start_day <= weekend.end and end_day >= weekend.start
        return True

    tabs = []
    for tab in rules["tabs"]:
        ids = [e["id"] for e in result["events"] if (when(e, tab["id"]) if tab["id"] in {"today", "tonight", "weekend", "upcoming"} else True)
               and (not tab.get("categories") or set(e["categories"]) & set(tab["categories"])) and (not tab.get("free") or e["price"]["kind"] == "free")]
        if ids:
            tabs.append({"id": tab["id"], "label": tab["label"], "count": len(ids), "event_ids": ids})
    return {**result, "tabs": tabs, "anchor": anchor.isoformat(), "weekend": weekend.as_dict(), "single_day": single(anchor).as_dict()}
