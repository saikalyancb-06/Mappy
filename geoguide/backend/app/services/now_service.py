"""The "Now" screen: a grounded briefing for the active reference."""
from __future__ import annotations

from typing import Any

from app.core.cache import cache_get, cache_set
from app.core.logging import Trace
from app.core.rules import taxonomy
from app.geo.geo_context import GeoContext, destination_by_id, distance_from_user
from app.geo.geocoding import reverse_geocode
from app.knowledge.context import active_advisories, events_for, weather_notices
from app.knowledge.destination_pack import get_pack
from app.llm.evidence import EvidenceBuilder
from app.llm.generator import generate
from app.llm.prompts import AnswerContext
from app.retrieval.knowledge import retrieve
from app.services.discovery import DiscoveryRequest, discover
from app.weather.open_meteo import get_weather, local_now

BRIEFING_TTL_S = 1800


def build_now(geo: GeoContext, profile: dict[str, Any], language: str = "en") -> dict[str, Any]:
    trace = Trace("now")
    reference = geo.reference
    destination = destination_by_id(reference.destination_id)
    pack = get_pack(destination.id) if destination else None
    errors: list[dict[str, str]] = list(geo.provider_errors)
    if pack:
        weather = pack["weather"]
        place = {**pack["destination"], "kind": "destination"}
    else:
        weather = get_weather(reference.lat, reference.lon, 0)
        named, reverse_errors = reverse_geocode(reference.lat, reference.lon)
        errors.extend(reverse_errors)
        place = {"name": (named or {}).get("name") or (reference.label if reference.label != "your location" else None), "region": (named or {}).get("region"), "country": (named or {}).get("country"), "curated": False, "kind": "area"}
    if weather.get("status") != "ok":
        errors.append(weather.get("error") or {"source": "open_meteo", "code": "unavailable", "message": "Weather unavailable"})
    tz = (destination.timezone if destination else None) or (weather.get("timezone") if weather.get("status") == "ok" else None)
    now_local = local_now(tz)
    advisories = pack["advisories"] if pack else weather_notices(weather)
    if destination and not pack:
        advisories = active_advisories(destination.id, now_local.date()) + advisories
    events = [e for e in (pack["events"] if pack else events_for(reference.destination_id, now_local.date())) if e.get("timing") in {"dated", "usually_this_time_of_year"}]

    suggestions = discover(DiscoveryRequest(
        reference=reference,
        profile_name="discovery",
        kinds=set(taxonomy()["attraction_kinds"]),
        user=profile,
        local_time=now_local,
        time_sensitive=True,
        weather_signals=weather.get("signals") or [],
        limit=8,
        user_point=geo.user_point(),
    ), trace=trace)
    errors.extend(suggestions.provider_errors)

    evidence = EvidenceBuilder()
    label = place.get("name") or reference.label
    if weather.get("status") == "ok":
        evidence.add_weather(weather, label)
    for advisory in advisories[:3]:
        evidence.add_advisory(advisory)
    for event in events[:2]:
        evidence.add_event(event)
    for candidate in suggestions.candidates[:4]:
        evidence.add_candidate(candidate, reference.distance_label, distance_from_user(geo, candidate.lat, candidate.lon) if reference.origin != "user_location" else None)
    if destination:
        for hit in retrieve("overview history significance highlights what makes this place special", destination_id=destination.id, kinds=["place_kb"], limit=2).hits:
            evidence.add_knowledge(hit)

    cache_key = f"{reference.destination_id or f'{reference.lat:.3f},{reference.lon:.3f}'}:{now_local.strftime('%Y-%m-%dT%H')}:{language}:{','.join(c.id for c in suggestions.candidates[:4])}"
    cached = cache_get("briefing", cache_key)
    if cached:
        briefing = cached["data"]
    else:
        context = AnswerContext(
            question=f"Give me a short briefing for {label} right now.",
            intent="BRIEFING",
            location_notes=[f"Briefing for {label} at local time {now_local.strftime('%A %H:%M')}."] + geo.warnings,
            evidence=evidence.items,
            guidance=["Write 3-4 sentences, no bullet list: what makes the place special, what matters today (weather, daylight, advisories), and one proactive suggestion from the evidence."],
            extra={"lead": f"{label} — {now_local.strftime('%A %H:%M')}.", "empty_message": "Live context for this area is limited right now."},
        )
        generated = generate(context, language=language, trace=trace, known_names=[c.name for c in suggestions.candidates])
        errors.extend(generated.errors)
        briefing = {"text": generated.text, "mode": generated.mode, "language": generated.language, "sources": [item.as_dict() for item in evidence.items]}
        if generated.mode != "deterministic":
            cache_set("briefing", cache_key, briefing, BRIEFING_TTL_S, source="groq")
    trace.emit()
    return {
        "request_id": trace.request_id,
        "geo_context": geo.as_dict(),
        "place": place,
        "local_time": now_local.isoformat(timespec="minutes"),
        "timezone": tz,
        "weather": weather,
        "daylight_left_min": weather.get("daylight_left_min") if weather.get("status") == "ok" else None,
        "advisories": advisories,
        "events": events,
        "suggestions": [c.as_dict() for c in suggestions.candidates],
        "briefing": briefing,
        "pack": {"version": pack["version"], "created_at": pack["created_at"], "provenance": pack["provenance"], "counts": pack["counts"]} if pack else None,
        "data_status": "partial" if errors else "live",
        "provider_errors": errors,
    }
