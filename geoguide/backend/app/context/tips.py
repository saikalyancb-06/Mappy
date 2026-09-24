"""Local tips from retrieved facts: weather for the date, season, events and stored etiquette.

The LLM never writes these. Each tip names the fact it rests on (``basis``), so
changing the date changes the tips only when the underlying data changes.
"""
from __future__ import annotations

import re
from typing import Any

from app.core.rules import load_rules
from app.services.cost import money_text, to_decimal


def _sentences(text: str, count: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(parts[:count]).strip()


def _fmt(value: float | None) -> str:
    return f"{value:.0f}" if value is not None else "?"


def build_tips(*, weather: dict[str, Any] | None, season: dict[str, Any] | None, events: list[dict[str, Any]], culture: list[Any], advisories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = load_rules("context")["tips"]
    text = rules["text"]
    tips: list[dict[str, Any]] = []

    def add(kind: str, message: str, basis: str, source: str | None = None, source_url: str | None = None) -> None:
        tips.append({"id": f"{kind}-{len(tips) + 1}", "kind": kind, "text": message, "basis": basis, "source": source, "source_url": source_url})

    day = (weather or {}).get("day") if (weather or {}).get("status") == "ok" else None
    if day:
        basis = f"{weather.get('basis_label') or 'Weather'} for {day.get('date')}"
        probability, rain_mm = day.get("precipitation_probability_max"), day.get("precipitation_mm")
        rainy = "rain" in (day.get("signals") or []) or (probability is not None and probability >= rules["rain_probability"]) or (rain_mm is not None and rain_mm >= rules["rain_mm"])
        if rainy:
            detail = f"{probability}% chance" if probability is not None else (f"{rain_mm} mm expected" if weather.get("basis") != "typical" else day.get("summary", "").lower())
            add("weather", text["rain"].format(detail=detail), basis, weather.get("source"), weather.get("source_url"))
        apparent = day.get("apparent_max_c")
        if apparent is not None and apparent >= rules["heat_apparent_c"]:
            add("weather", text["heat"].format(apparent=_fmt(apparent)), basis, weather.get("source"), weather.get("source_url"))
        if day.get("uv_index_max") is not None and day["uv_index_max"] >= rules["uv_high"]:
            add("weather", text["uv"].format(uv=_fmt(day["uv_index_max"])), basis, weather.get("source"), weather.get("source_url"))
        low, high = day.get("temp_min_c"), day.get("temp_max_c")
        if low is not None and low <= rules["cool_min_c"]:
            add("weather", text["cool"].format(min=_fmt(low)), basis, weather.get("source"), weather.get("source_url"))
        elif not rainy and low is not None and high is not None and rules["mild_max_c"][0] <= high <= rules["mild_max_c"][1]:
            add("weather", text["mild"].format(min=_fmt(low), max=_fmt(high)), basis, weather.get("source"), weather.get("source_url"))

    for event in events:
        source = event.get("source") or {}
        dates = event["start_date"] if event.get("start_date") == event.get("end_date") else f"{event.get('start_date')} to {event.get('end_date')}"
        if event.get("crowded") or event.get("type") == "festival":
            add("event", text["crowd"].format(event=event["name"], dates=dates), f"{event['name']} listing" + (f" (expected footfall {event['expected_footfall']:,})" if event.get("expected_footfall") else ""), source.get("name"), source.get("url"))
        if event.get("is_ticketed"):
            amount = money_text(to_decimal(event["ticket_price"]), event.get("currency")) if event.get("ticket_price") else None
            price = f" (from {amount})" if amount else ""
            add("event", text["ticketed"].format(event=event["name"], price=price), f"{event['name']} listing", source.get("name"), source.get("url"))
        if event.get("etiquette"):
            add("etiquette", event["etiquette"], f"{event['name']} listing", source.get("name"), source.get("url"))
        if len(tips) >= 8:
            break

    if season and season.get("peak_tourist_season") is not None:
        add("season", text["peak"] if season["peak_tourist_season"] else text["off_peak"], f"Peak months in the city data: {', '.join(str(m) for m in season.get('peak_months') or [])}")

    for hit in culture:
        if getattr(hit, "category", None) == "etiquette" or "etiquette" in (getattr(hit, "title", "") or "").lower():
            add("etiquette", _sentences(hit.content, 2), f"Local etiquette — {hit.title or 'stored knowledge'}", getattr(hit, "source", None), getattr(hit, "source_url", None))
            break

    for advisory in advisories[:2]:
        add("safety", text["advisory"].format(title=advisory.get("title"), body=_sentences(advisory.get("body") or "", 1)), f"Advisory ({advisory.get('severity')}) from {advisory.get('issuing_body') or advisory.get('source') or 'stored data'}", advisory.get("source"), advisory.get("source_url"))
    return tips
