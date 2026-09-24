"""Official festival and holiday calendars (data/sources/festival_calendars/*.json).

Each file is one published government calendar for a country, optionally limited to
states/regions. Entries are either dated for a specific year (``date``) or fixed every
year (``annual``: "MM-DD"). They are day-long observances across the city, not
ticketed events, so they carry no venue or price. Nothing is inferred: a festival
whose official date is not in a file does not appear.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import FESTIVAL_CALENDARS_DIR
from app.core.rules import load_rules
from app.core.text import normalize
from app.events.model import EventQuery, EventSource, NormalisedEvent
from app.events.providers.base import EventProvider, ProviderResult
from app.events.taxonomy import categorise


@lru_cache(maxsize=4)
def load_calendars(directory: str) -> tuple[dict[str, Any], ...]:
    folder = Path(directory)
    if not folder.is_dir():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json")))


def _days(entry: dict[str, Any], first: date, last: date) -> list[date]:
    if entry.get("date"):
        day = date.fromisoformat(entry["date"])
        return [day] if first <= day <= last else []
    if entry.get("annual"):
        month, day_of_month = (int(part) for part in entry["annual"].split("-"))
        found = []
        for year in range(first.year, last.year + 1):
            try:
                day = date(year, month, day_of_month)
            except ValueError:
                continue
            if first <= day <= last:
                found.append(day)
        return found
    return []


class CalendarProvider(EventProvider):
    name, label = "calendar", "Official festival and holiday calendars"
    local = True

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = str(directory or FESTIVAL_CALENDARS_DIR)

    def calendars_for(self, query: EventQuery) -> list[dict[str, Any]]:
        from app.geo.city import country_code

        code = (country_code(query.city) or "").upper()
        region = normalize(query.city.region or "")
        chosen = []
        for calendar in load_calendars(self.directory):
            if (calendar.get("country") or "").upper() != code:
                continue
            regions = {normalize(r) for r in calendar.get("regions") or []}
            if regions and region not in regions:
                continue
            chosen.append(calendar)
        return chosen

    def search_events(self, query: EventQuery) -> ProviderResult:
        started = self._timed()
        result = ProviderResult(self.name, self.label)
        first, last = query.start.date(), query.end.date()
        reliability = load_rules("events")["sources"]["official_calendar"]
        seen: set[tuple[str, date]] = set()
        for calendar in self.calendars_for(query):
            for entry in calendar.get("entries", []):
                result.raw_count += 1
                for day in _days(entry, first, last):
                    key = (normalize(entry["title"]), day)
                    if key in seen:
                        result.drop("duplicate")
                        continue
                    seen.add(key)
                    result.events.append(self._event(calendar, entry, day, query, reliability))
        result.latency_ms = int((self._timed() - started) * 1000)
        return result

    def _event(self, calendar: dict[str, Any], entry: dict[str, Any], day: date, query: EventQuery, reliability: float) -> NormalisedEvent:
        notes = [calendar.get("note") or ""]
        if entry.get("moon_dependent"):
            notes.append("The date depends on the sighting of the moon and may shift by a day.")
        description = " ".join(n for n in notes if n).strip()
        category, categories, _ = categorise(entry["title"], description, None, None)
        wanted = entry.get("category")
        if wanted:
            categories = list(dict.fromkeys([wanted, *[c for c in categories if c != "other"]]))
            category = wanted
        source = EventSource(provider=self.name, name=calendar.get("authority") or calendar["title"], kind="official_calendar", reliability=reliability,
                             source_event_id=f"{calendar['id']}:{normalize(entry['title']).replace(' ', '-')}:{day.isoformat()}", url=calendar.get("source_url"),
                             last_updated=calendar.get("verified_on"))
        return NormalisedEvent(
            title=entry["title"], description=description, start=datetime.combine(day, time(0, 0), tzinfo=query.tz), end=datetime.combine(day, time(23, 59), tzinfo=query.tz),
            timezone=query.city.timezone or "UTC", all_day=True, time_known=False, source=source, category=category, categories=categories,
            type="festival" if entry.get("kind") == "festival" else "event", city=query.city.name, price_kind="unknown", freshness="stored",
        )

    def health_check(self) -> dict[str, Any]:
        calendars = load_calendars(self.directory)
        return {"provider": self.name, "configured": bool(calendars), "calendars": [c.get("title") for c in calendars]}
