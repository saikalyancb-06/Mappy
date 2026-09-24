"""What's happening in a city on a date (or over a range).

Hard rule: the result is exactly the verified records that match the city and
the dates. When there are none, ``events`` is empty and ``event_status`` is
``no_verified_events_found``; nothing is substituted, not even a festival the
city is known for (those appear only under ``associated_festivals``, labelled
as unconfirmed for these dates).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.dates import DateRange
from app.core.rules import load_rules
from app.events.live import refresh_live_events
from app.events.store import associated_festivals, events_in_city
from app.geo.city import City
from app.search.serpapi import SerpApiClient

FOUND = "verified_events_found"
NONE = "no_verified_events_found"


def no_events_message(city: City, window: DateRange) -> str:
    when = f"on {window.label}" if window.start == window.end else f"for {window.label}"
    return f"No verified events found for {city.name} {when} in the available sources."


def city_events(city: City, window: DateRange, *, today: date, user_point: tuple[float, float] | None = None, live: bool = True, web: SerpApiClient | None = None) -> dict[str, Any]:
    sources = refresh_live_events(city, window, today, web) if live else []
    events = events_in_city(city, window, user_point)
    groups_config = load_rules("events")["groups"]
    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(event["category"], []).append(event["id"])
    groups = [{"id": gid, "label": groups_config.get(gid, groups_config["other"])["label"], "event_ids": ids} for gid, ids in sorted(grouped.items(), key=lambda item: groups_config.get(item[0], groups_config["other"])["order"])]
    checked = [{"source": "stored events and festivals (curated + organiser dataset)", "status": "ok"}] + sources
    return {
        "city": city.as_dict(),
        "range": window.as_dict(),
        "event_status": FOUND if events else NONE,
        "message": None if events else no_events_message(city, window),
        "events": events,
        "festivals": [e for e in events if e["type"] == "festival"],
        "live_events": [e for e in events if e["type"] != "festival"],
        "groups": groups,
        "associated_festivals": associated_festivals(city, window),
        "sources_checked": checked,
    }
