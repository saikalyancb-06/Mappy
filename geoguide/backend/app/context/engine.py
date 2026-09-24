"""City-and-date context engine.

    City  = WHERE (the boundary for events, festivals, knowledge and weather)
    Date  = WHEN  (changing it re-runs every source, not just the label)
    Intent = WHAT (the Explore tabs and Ask)

For a city and a selected date it gathers, each from its own source:
place knowledge (RAG, place domain), culture/etiquette (RAG, culture domain),
events and festivals (city + date overlap, never a radius), weather for that
date (forecast / recorded / dataset / typical, labelled), the season, active
advisories, top attractions, and rule-based tips. Only then does the LLM write
a short briefing from that evidence, under the hard rule that an empty event
result stays empty.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from typing import Any

from app.context.season import season_for
from app.context.tips import build_tips
from app.core.cache import cache_get, cache_set
from app.core.dates import DateRange, single
from app.core.logging import Trace
from app.core.rules import load_rules, taxonomy
from app.events.service import city_events
from app.geo.city import City
from app.geo.geo_context import ActiveReference, destination_by_id
from app.knowledge.context import SERIOUS, active_advisories, weather_notices
from app.llm.evidence import EvidenceBuilder
from app.llm.generator import generate
from app.llm.prompts import AnswerContext
from app.retrieval.knowledge import KnowledgeHit, retrieve
from app.search.serpapi import SerpApiClient
from app.services.discovery import DiscoveryRequest, discover
from app.weather.open_meteo import local_now, weather_on

BRIEFING_TTL_S = 1800


def city_reference(city: City) -> ActiveReference:
    destination = destination_by_id(city.destination_id)
    radius = float(destination.coverage_radius_km) if destination and destination.coverage_radius_km else 10.0
    return ActiveReference(origin="active_destination", lat=city.lat, lon=city.lon, label=city.name, radius_km=radius, semantic="in_destination", destination_id=city.destination_id)


def knowledge(city: City, domain: str, sections: list[str] | None = None, limit: int = 3, query: str | None = None) -> list[KnowledgeHit]:
    """Domain-scoped retrieval: filter by city and knowledge section first, then rank."""
    if not city.destination_id:
        return []
    config = load_rules("context")["rag_domains"][domain]
    return retrieve(query or config["query"], destination_id=city.destination_id, kinds=["place_kb"], categories=sections or config["categories"], limit=limit).hits


def _date_info(selected: date, today: date) -> dict[str, Any]:
    delta = (selected - today).days
    return {"selected": selected.isoformat(), "today": today.isoformat(), "days_from_today": delta, "kind": "today" if delta == 0 else ("past" if delta < 0 else "future"), "label": single(selected).label}


def build_city_context(
    city: City,
    selected: date | None = None,
    *,
    profile: dict[str, Any] | None = None,
    user_point: tuple[float, float] | None = None,
    language: str = "en",
    web: SerpApiClient | None = None,
    live_events: bool = True,
) -> dict[str, Any]:
    trace = Trace("city_context")
    config = load_rules("context")
    today = local_now(city.timezone).date()
    selected = selected or today
    window: DateRange = single(selected)
    errors: list[dict[str, str]] = []

    season = season_for(city, selected)
    weather = weather_on(city.lat, city.lon, selected, timezone_name=city.timezone, destination_id=city.destination_id)
    if weather.get("status") != "ok":
        errors.append(weather.get("error") or {"source": "open_meteo", "code": "unavailable", "message": "Weather unavailable"})
    events = city_events(city, window, today=today, user_point=user_point, live=live_events, web=web)
    errors.extend(s["error"] for s in events["sources_checked"] if s.get("status") == "error" and s.get("error"))
    advisories = active_advisories(city.destination_id, selected) + (weather_notices(weather) if weather.get("basis") == "forecast" else [])

    about = knowledge(city, "place", config["about_sections"], limit=4)
    culture = knowledge(city, "culture", config["culture_sections"], limit=3)

    reference = city_reference(city)
    local_time = local_now(city.timezone) if selected == today else datetime.combine(selected, time(10, 0), tzinfo=local_now(city.timezone).tzinfo)
    attractions = discover(DiscoveryRequest(
        reference=reference, profile_name="discovery", kinds=set(taxonomy()["attraction_kinds"]), user=profile or {},
        local_time=local_time, time_sensitive=selected == today, weather_signals=weather.get("signals") or [],
        limit=config["attractions_limit"], user_point=user_point,
    ), trace=trace)
    errors.extend(attractions.provider_errors)
    if selected != today:
        # "Open now" only means something today; other dates keep the stored opening hours instead.
        for candidate in attractions.candidates:
            candidate.open_status, candidate.open_detail = "unknown", {}

    tips = build_tips(weather=weather, season=season, events=events["events"], culture=culture, advisories=[a for a in advisories if a.get("severity") in SERIOUS])
    trace.step("city_context", city=city.key, date=selected.isoformat(), events=len(events["events"]), weather_basis=weather.get("basis"), about=len(about), culture=len(culture), attractions=len(attractions.candidates), tips=len(tips))

    # ---- grounded briefing
    evidence = EvidenceBuilder()
    for hit in about[:2]:
        evidence.add_knowledge(hit)
    evidence.add_event_status(city.name, window.label, len(events["events"]), [s["source"] for s in events["sources_checked"] if s.get("status") in {"ok", None}])
    for event in events["events"][:5]:
        evidence.add_event(event)
    if weather.get("status") == "ok":
        evidence.add_weather(weather, city.name)
    for advisory in [a for a in advisories if a.get("severity") in SERIOUS][:2]:
        evidence.add_advisory(advisory)
    for candidate in attractions.candidates[:4]:
        evidence.add_candidate(candidate, reference.distance_label)

    fingerprint = hashlib.sha1("|".join([item.title + item.content for item in evidence.items]).encode()).hexdigest()[:12]
    cache_key = f"{city.key}:{selected.isoformat()}:{language}:{fingerprint}"
    cached = cache_get("city_briefing", cache_key)
    if cached:
        briefing = cached["data"]
    else:
        when = "today" if selected == today else f"on {window.label}"
        context = AnswerContext(
            question=f"Give me a short briefing for {city.label} {when}.",
            intent="BRIEFING",
            location_notes=[f"City: {city.label}. Selected date: {window.label} ({_date_info(selected, today)['kind']}).", f"Weather source for this date: {weather.get('basis_label') or 'unavailable'}."],
            evidence=evidence.items,
            guidance=[
                "Write 3-5 sentences, no bullet list.",
                "1) What the place is known for, from the knowledge evidence. 2) What is happening on the selected date: only the events in the evidence, with dates; if the VERIFIED EVENTS item says None, say plainly that no verified events are listed for this date and suggest nothing in their place. 3) The weather for the date, saying whether it is a forecast, a record or typical conditions. 4) One suggestion from the attractions.",
            ],
            extra={"lead": f"{city.name} — {window.label}.", "empty_message": f"I have little verified information for {city.name} on {window.label}.", "no_events_message": events["message"]},
        )
        generated = generate(context, language=language, trace=trace, known_names=[c.name for c in attractions.candidates] + [e["name"] for e in events["events"]])
        errors.extend(generated.errors)
        briefing = {"text": generated.text, "mode": generated.mode, "language": generated.language, "validation": generated.validation.as_dict() if generated.validation else None, "sources": [item.as_dict() for item in evidence.items]}
        if generated.mode != "deterministic":
            cache_set("city_briefing", cache_key, briefing, BRIEFING_TTL_S, source="groq")
    trace.emit()

    return {
        "request_id": trace.request_id,
        "city": city.as_dict(),
        "date": _date_info(selected, today),
        "local_time": local_now(city.timezone).isoformat(timespec="minutes") if selected == today else None,
        "season": season,
        "weather": weather,
        "events": events,
        "advisories": advisories,
        "about": [hit.as_dict() for hit in about],
        "culture": [hit.as_dict() for hit in culture],
        "attractions": [c.as_dict() for c in attractions.candidates],
        "tips": tips,
        "briefing": briefing,
        "data_status": "partial" if errors else "complete",
        "provider_errors": errors,
    }
