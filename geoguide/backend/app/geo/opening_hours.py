"""Opening-hours evaluation.

Structured format stored in ``pois.opening_hours`` (JSON)::

    {"always_open": true}
    {"weekly": {"daily": [["06:00", "13:00"], ["17:00", "21:00"]]}}
    {"weekly": {"mon": [...], "tue": [...], ...}, "closed": ["fri"], "notes": "..."}

A missing or unparseable value means *unknown*, never "open".
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from typing import Any

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_OSM_DAYS = {"mo": "mon", "tu": "tue", "we": "wed", "th": "thu", "fr": "fri", "sa": "sat", "su": "sun"}


def load(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def parse_osm(raw: str | None) -> dict[str, Any] | None:
    """Parse the common subset of OSM ``opening_hours`` syntax; return None otherwise."""
    if not raw:
        return None
    text = raw.strip()
    if text == "24/7":
        return {"always_open": True}
    weekly: dict[str, list[list[str]]] = {}
    for rule in [part.strip() for part in text.split(";") if part.strip()]:
        match = re.fullmatch(r"((?:[A-Za-z]{2}(?:-[A-Za-z]{2})?,?)+)?\s*((?:\d{1,2}:\d{2}-\d{1,2}:\d{2},?)+|off)", rule)
        if not match:
            return None
        day_spec, time_spec = match.group(1), match.group(2)
        days: list[str] = []
        for chunk in (day_spec or "Mo-Su").split(","):
            chunk = chunk.strip().lower()
            if not chunk:
                continue
            if "-" in chunk:
                start, end = chunk.split("-")
                if start not in _OSM_DAYS or end not in _OSM_DAYS:
                    return None
                i, j = DAYS.index(_OSM_DAYS[start]), DAYS.index(_OSM_DAYS[end])
                days.extend(DAYS[i : j + 1] if i <= j else DAYS[i:] + DAYS[: j + 1])
            elif chunk in _OSM_DAYS:
                days.append(_OSM_DAYS[chunk])
            else:
                return None
        intervals = [] if time_spec == "off" else [span.split("-") for span in time_spec.split(",") if span]
        for day in days:
            weekly[day] = intervals
    return {"weekly": weekly} if weekly else None


def _intervals_for(hours: dict[str, Any], day: str) -> list[tuple[time, time]] | None:
    if hours.get("always_open"):
        return [(time(0, 0), time(23, 59))]
    weekly = hours.get("weekly")
    if not isinstance(weekly, dict):
        return None
    if day in (hours.get("closed") or []):
        return []
    spans = weekly.get(day, weekly.get("daily"))
    if spans is None:
        return []
    parsed = []
    for span in spans:
        try:
            start = datetime.strptime(span[0], "%H:%M").time()
            end = datetime.strptime(span[1], "%H:%M").time()
            parsed.append((start, end))
        except (ValueError, IndexError, TypeError):
            return None
    return parsed


def status_at(hours_value: Any, local_dt: datetime) -> dict[str, Any]:
    """Return {'status': open|closed|unknown, 'closes_at'?, 'opens_at'?}."""
    hours = load(hours_value)
    if not hours:
        return {"status": "unknown"}
    if hours.get("always_open"):
        return {"status": "open", "always_open": True}
    day = DAYS[local_dt.weekday()]
    intervals = _intervals_for(hours, day)
    if intervals is None:
        return {"status": "unknown"}
    current = local_dt.time()
    for start, end in intervals:
        if start <= current < end:
            return {"status": "open", "closes_at": end.strftime("%H:%M")}
    later = sorted(start for start, _ in intervals if start > current)
    if later:
        return {"status": "closed", "opens_at": later[0].strftime("%H:%M")}
    # Find next day's opening for a helpful message.
    for offset in range(1, 8):
        next_day = local_dt + timedelta(days=offset)
        next_intervals = _intervals_for(hours, DAYS[next_day.weekday()]) or []
        if next_intervals:
            return {"status": "closed", "opens_at": f"{DAYS[next_day.weekday()].title()} {min(s for s, _ in next_intervals).strftime('%H:%M')}"}
    return {"status": "closed"}


def open_for_window(hours_value: Any, start: datetime, end: datetime) -> bool | None:
    """True if one opening interval covers [start, end]; None when hours are unknown."""
    hours = load(hours_value)
    if not hours:
        return None
    intervals = _intervals_for(hours, DAYS[start.weekday()])
    if intervals is None:
        return None
    for span_start, span_end in intervals:
        if span_start <= start.time() and (end.time() <= span_end or (span_end == time(23, 59) and end.date() > start.date())) and end.date() == start.date():
            return True
    return False


def describe(hours_value: Any) -> str | None:
    hours = load(hours_value)
    if not hours:
        return None
    if hours.get("always_open"):
        return "Open 24 hours"
    weekly = hours.get("weekly") or {}
    parts = []
    if "daily" in weekly:
        parts.append("Daily " + ", ".join(f"{s}–{e}" for s, e in weekly["daily"]))
    for day in DAYS:
        if day in weekly:
            spans = weekly[day]
            parts.append(f"{day.title()} " + (", ".join(f"{s}–{e}" for s, e in spans) if spans else "closed"))
    if hours.get("closed"):
        parts.append("Closed " + ", ".join(day.title() for day in hours["closed"]))
    if hours.get("notes"):
        parts.append(str(hours["notes"]))
    return "; ".join(parts) or None
