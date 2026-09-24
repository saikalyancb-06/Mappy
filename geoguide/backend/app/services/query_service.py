"""QueryService: the canonical /ask pipeline.

question → QueryIntent → GeoContext → route → retrieval (spatial / entity /
knowledge / weather / safety / events / web) → evidence → grounded LLM →
validation → structured response with sources, context and a trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.core.logging import Trace
from app.core.rules import load_rules, taxonomy
from app.geo import opening_hours
from app.geo.geo_context import GeoContext, build_geo_context, destination_by_id, distance_from_user
from app.geo.geocoding import destination_to_place, resolve_place
from app.geo.spatial import get_pois
from app.entities.resolver import resolve_entity
from app.knowledge.context import SERIOUS, active_advisories, events_for, weather_notices, web_events
from app.llm.evidence import EvidenceBuilder
from app.llm.generator import generate
from app.llm.prompts import AnswerContext
from app.models import Candidate
from app.planning.itinerary import PlanRequest, optimise
from app.query.llm_parser import refine_with_llm
from app.query.models import IntentType, QueryIntent
from app.query.parser import parse_query
from app.retrieval.knowledge import retrieve
from app.search.normalizer import web_evidence
from app.search.serpapi import SearchProviderError, client as serp_client
from app.ranking.ranker import RankRequest, rank
from app.services.discovery import DiscoveryRequest, discover
from app.services.route_suggestions import RouteRequest, route_suggestions
from app.weather.open_meteo import get_weather, local_now

ATTRACTION_KINDS = set(taxonomy()["attraction_kinds"])


@dataclass
class AskRequest:
    question: str
    user_location: dict[str, Any] | None = None
    active_destination: dict[str, Any] | str | None = None
    selected_place_id: str | None = None
    profile: dict[str, Any] = field(default_factory=dict)
    language: str = "en"
    debug: bool = False


def _reference_timezone(geo: GeoContext, weather: dict[str, Any] | None = None) -> str | None:
    destination = destination_by_id(geo.reference.destination_id) if geo.reference and geo.reference.destination_id else None
    if destination and destination.timezone:
        return destination.timezone
    if weather and weather.get("status") == "ok":
        return weather.get("timezone")
    return None


def _location_notes(geo: GeoContext) -> list[str]:
    notes = []
    if geo.reference:
        meaning = {"near_me": "around the traveller's current location", "in_destination": f"within the {geo.reference.label} area", "near_place": f"around {geo.reference.label}"}[geo.reference.semantic]
        notes.append(f"This answer is about places {meaning} (radius {geo.reference.radius_km:g} km).")
    if geo.user_location is None:
        notes.append("The traveller's device location is unavailable; do not describe anything as near them.")
    else:
        user = geo.user_location
        where = f"inside {user.area_name}" if user.area_name else "outside any curated destination"
        notes.append(f"The traveller's device location is known ({user.freshness}, accuracy {user.accuracy_level}), {where}.")
        if geo.reference and geo.reference.origin != "user_location":
            notes.append(f"The traveller is {distance_from_user(geo, geo.reference.lat, geo.reference.lon):.1f} km from {geo.reference.label}.")
    notes.extend(geo.warnings)
    return notes


def _expanded_query(intent: QueryIntent) -> str:
    """Add taxonomy synonyms for stated preferences ("family" → kids, children…) to the retrieval query."""
    extra = [phrase for preference in intent.preferences for phrase in taxonomy()["preferences"].get(preference, [])[:4]]
    return " ".join([intent.raw_query, *extra])


def _day_offset(intent: QueryIntent) -> int:
    return intent.temporal.day_offset if intent.temporal else 0


class QueryService:
    def ask(self, request: AskRequest) -> dict[str, Any]:
        trace = Trace("ask", request.question)
        intent = parse_query(request.question, has_selected_entity=bool(request.selected_place_id))
        intent = refine_with_llm(intent)
        # A named place that is a destination ("tell me about <destination>") is destination knowledge.
        if intent.entity_mention and intent.intent == IntentType.PLACE_LOOKUP:
            place, _ = resolve_place(intent.entity_mention, allow_remote=False)
            if place and place.kind == "destination":
                intent.intent = IntentType.DESTINATION_KNOWLEDGE
                intent.place_mention, intent.entity_mention, intent.spatial_relation = intent.entity_mention, None, "in_place"
        if intent.entity_mention and intent.intent != IntentType.PLACE_LOOKUP and not intent.place_mention:
            intent.place_mention, intent.entity_mention = intent.entity_mention, None
            intent.spatial_relation = intent.spatial_relation or "in_place"
        trace.set("intent", intent.as_dict())

        place_for_geo = None if intent.intent in {IntentType.PLACE_LOOKUP, IntentType.ROUTE_SUGGESTIONS, IntentType.COMPARE} else intent.place_mention
        geo = build_geo_context(user_location=request.user_location, active_destination=request.active_destination, place_mention=place_for_geo, spatial_relation=intent.spatial_relation if intent.intent != IntentType.PLACE_LOOKUP else None, radius_km=intent.radius_km)
        if intent.intent == IntentType.NEARBY_SEARCH and geo.reference and geo.reference.semantic == "near_me" and not intent.radius_km:
            geo.reference.radius_km = float(load_rules("intents")["default_radius_km"]["NEARBY_SEARCH"])
        trace.set("geo_context", geo.as_dict())
        for error in geo.provider_errors:
            trace.error(error["source"], error["code"], error["message"])

        state = _State(request=request, intent=intent, geo=geo, trace=trace)
        handler = {
            IntentType.WEATHER: self._weather,
            IntentType.SAFETY: self._safety,
            IntentType.NEARBY_SEARCH: self._nearby,
            IntentType.ACTIVITY_DISCOVERY: self._discovery,
            IntentType.RECOMMENDATION: self._discovery,
            IntentType.PLACE_LOOKUP: self._lookup,
            IntentType.ITINERARY: self._itinerary,
            IntentType.LIVE_INFORMATION: self._events,
            IntentType.WEB_RESEARCH: self._web,
            IntentType.ROUTE_SUGGESTIONS: self._route,
            IntentType.COMPARE: self._compare,
        }.get(intent.intent, self._knowledge)
        trace.set("route", handler.__name__.strip("_"))
        handler(state)
        return self._respond(state)

    # ---- routes -----------------------------------------------------------------

    def _require_reference(self, state: "_State") -> bool:
        if state.geo.reference:
            return True
        need = state.geo.reference_required
        if need == "user_location":
            state.extra["empty_message"] = "I need your current location for “near me” questions. Turn on location access, or choose a destination to explore."
        elif need == "resolvable_place":
            state.extra["empty_message"] = f"I couldn't find “{state.intent.place_mention}”. Try a more specific name or pick a destination."
        else:
            state.extra["empty_message"] = "Tell me where you are (turn on location) or choose a destination, and I'll answer for that place."
        state.needs = need
        return False

    def _weather(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        reference = state.geo.reference
        state.trace.start_timer("weather")
        weather = get_weather(reference.lat, reference.lon, _day_offset(state.intent))
        state.trace.stop_timer("weather")
        state.weather = weather
        label = reference.label
        if weather.get("status") != "ok":
            state.errors.append(weather.get("error") or {"source": "open_meteo", "code": "unavailable", "message": "Weather is unavailable."})
            state.extra["empty_message"] = f"Live weather for {label} is unavailable right now, so I can't say whether it will rain. Please try again shortly."
            return
        state.evidence.add_weather(weather, label)
        state.extra["lead"] = f"Weather for {label}:"

    def _safety(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        reference = state.geo.reference
        weather = get_weather(reference.lat, reference.lon, _day_offset(state.intent))
        state.weather = weather
        today = local_now(_reference_timezone(state.geo, weather)).date() + timedelta(days=_day_offset(state.intent))
        advisories = active_advisories(reference.destination_id, today) + weather_notices(weather)
        state.safety = advisories
        for advisory in advisories:
            state.evidence.add_advisory(advisory)
        if weather.get("status") == "ok":
            state.evidence.add_weather(weather, reference.label)
        if reference.destination_id:
            result = retrieve(state.intent.raw_query, destination_id=reference.destination_id, kinds=["place_kb"], limit=2)
            for hit in result.hits:
                if hit.scores.get("bm25", 0) > 0:
                    state.evidence.add_knowledge(hit)
        if not advisories:
            state.extra["closing_notes"] = ["No active safety advisories are recorded for this area. This is not a guarantee of safety; follow local guidance."]
        state.trace.step("safety", advisories=[a["id"] for a in advisories])

    def _nearby(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        intent = state.intent
        local_time = local_now(_reference_timezone(state.geo))
        weather = None
        if intent.weather_required:
            weather = get_weather(state.geo.reference.lat, state.geo.reference.lon, _day_offset(intent))
            state.weather = weather
        result = discover(DiscoveryRequest(
            reference=state.geo.reference,
            profile_name="nearby",
            category=intent.category,
            group=intent.category_group if not intent.category else None,
            preferences=intent.preferences,
            query_text=intent.raw_query,
            user=state.request.profile,
            local_time=local_time,
            time_sensitive=bool(intent.live_required or (intent.temporal and intent.temporal.label in {"now", "tonight"})),
            require_open=bool(intent.live_required),
            weather_signals=(weather or {}).get("signals") or [],
            user_point=state.geo.user_point(),
            constraints=intent.constraints,
        ), trace=state.trace)
        state.errors.extend(result.provider_errors)
        state.candidates = result.candidates
        for candidate in result.candidates[:6]:
            state.evidence.add_candidate(candidate, state.geo.reference.distance_label, distance_from_user(state.geo, candidate.lat, candidate.lon) if state.geo.reference.origin != "user_location" else None)
        label = taxonomy()["categories"].get(intent.category or "", {}).get("label", "places")
        if not result.candidates:
            state.extra["empty_message"] = f"I couldn't find any verified {label.lower()} results within {state.geo.reference.radius_km:g} km of {state.geo.reference.label}." + (" Web search is not configured, so only stored places were checked." if not serp_client.configured else "")
        state.extra["lead"] = f"Top {label.lower()} results {'near you' if state.geo.reference.semantic == 'near_me' else 'around ' + state.geo.reference.label}:"

    def _discovery(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        intent, reference = state.intent, state.geo.reference
        tz = _reference_timezone(state.geo)
        weather = None
        if intent.weather_required or intent.temporal:
            weather = get_weather(reference.lat, reference.lon, _day_offset(intent))
            state.weather = weather
            tz = tz or (weather.get("timezone") if weather.get("status") == "ok" else None)
        local_time = local_now(tz)
        if intent.temporal and intent.temporal.day_offset:
            local_time = (local_time + timedelta(days=intent.temporal.day_offset)).replace(hour=9, minute=0)
        elif intent.temporal and intent.temporal.part_of_day == "evening" and local_time.hour < 17:
            local_time = local_time.replace(hour=17, minute=30)
        time_sensitive = bool(intent.temporal) or intent.live_required
        result = discover(DiscoveryRequest(
            reference=reference,
            profile_name="discovery",
            category=intent.category,
            group=intent.category_group,
            kinds=None if (intent.category or intent.category_group) else ATTRACTION_KINDS,
            preferences=intent.preferences,
            query_text=intent.raw_query,
            user=state.request.profile,
            local_time=local_time,
            time_sensitive=time_sensitive,
            require_open=bool(intent.live_required and intent.temporal and intent.temporal.label == "now"),
            weather_signals=(weather or {}).get("signals") or [],
            user_point=state.geo.user_point(),
            constraints=intent.constraints,
        ), trace=state.trace)
        state.errors.extend(result.provider_errors)
        state.candidates = result.candidates
        if weather and weather.get("status") == "ok":
            state.evidence.add_weather(weather, reference.label)
        elif weather:
            state.errors.append(weather.get("error") or {"source": "open_meteo", "code": "unavailable", "message": "Weather unavailable"})
            state.extra.setdefault("closing_notes", []).append("Live weather is unavailable, so these suggestions don't account for conditions.")
        user_distance = reference.origin != "user_location"
        for candidate in result.candidates[:6]:
            state.evidence.add_candidate(candidate, reference.distance_label, distance_from_user(state.geo, candidate.lat, candidate.lon) if user_distance else None)
        if reference.destination_id:
            hits = retrieve(intent.raw_query, destination_id=reference.destination_id, kinds=["place_kb"], limit=2).hits
            for hit in hits:
                if hit.scores.get("bm25", 0) > 0 or hit.scores.get("vector", 0) > 0.4:
                    state.evidence.add_knowledge(hit)
            day = local_time.date()
            advisories = [a for a in active_advisories(reference.destination_id, day) if a["severity"] in SERIOUS] + weather_notices(weather)
            state.safety = advisories
            for advisory in advisories[:3]:
                state.evidence.add_advisory(advisory)
            if intent.events_required:
                events = [e for e in events_for(reference.destination_id, day) if e.get("timing") in {"dated", "usually_this_time_of_year"}]
                state.events = events
                for event in events[:3]:
                    state.evidence.add_event(event)
        if not result.candidates:
            state.extra["empty_message"] = f"I couldn't find verified places to suggest within {reference.radius_km:g} km of {reference.label}."
        state.extra["lead"] = f"Good options {'near you' if reference.semantic == 'near_me' else 'in ' + reference.label if reference.semantic == 'in_destination' else 'around ' + reference.label}" + (f" for {intent.temporal.label}" if intent.temporal and intent.temporal.label in {'today', 'tonight', 'tomorrow', 'weekend'} else "") + ":"

    def _lookup(self, state: "_State") -> None:
        intent, geo = state.intent, state.geo
        entity: Candidate | None = None
        if not intent.entity_mention and state.request.selected_place_id:
            found = get_pois([state.request.selected_place_id], geo.user_point())
            entity = found[0] if found else None
            state.resolution = {"status": "resolved" if entity else "not_found", "mention": "selected place", "entity_id": state.request.selected_place_id, "confidence": 1.0 if entity else 0.0}
        elif intent.entity_mention:
            reference = geo.user_point() or ((geo.active_destination.lat, geo.active_destination.lon) if geo.active_destination else None)
            category_hint = None
            for category_id, entry in taxonomy()["categories"].items():
                if any(f" {syn} " in f" {intent.entity_mention.lower()} " for syn in entry["synonyms"]):
                    category_hint = category_id
                    break
            resolution = resolve_entity(intent.entity_mention, locality_hint=intent.locality_hint, category_hint=category_hint, reference=reference, prefer_live=intent.live_required, trace=state.trace)
            state.resolution = resolution.as_dict()
            if resolution.status == "resolved":
                entity = resolution.entity
            elif resolution.status == "ambiguous":
                state.intent.intent = IntentType.PLACE_DISAMBIGUATION
                options = resolution.alternatives[:4]
                state.candidates = [option.candidate for option in options]
                for option in options:
                    state.evidence.add_candidate(option.candidate, None, distance_from_user(geo, option.candidate.lat, option.candidate.lon))
                where = f" in {intent.locality_hint}" if intent.locality_hint else ""
                state.extra["lead"] = f"I found more than one match for “{intent.entity_mention}”{where} and can't tell which you mean:"
                state.extra["closing_notes"] = ["Which one did you mean? You can reply with the name or area."]
                state.clarification = {"question": "Which one did you mean?", "options": [{"id": o.candidate.id, "name": o.candidate.name, "address": o.candidate.address} for o in options]}
                return
            else:
                where = f" in {intent.locality_hint}" if intent.locality_hint else ""
                state.extra["empty_message"] = f"I couldn't verify a place called “{intent.entity_mention}”{where}." + ("" if serp_client.configured else " Web search is not configured, so only stored places were checked.") + " Check the spelling or add the area name."
                for note in resolution.notes:
                    state.extra.setdefault("closing_notes", []).append(note)
                return
        if entity is None:
            state.extra["empty_message"] = "Which place do you mean? Open a place or mention its name."
            return
        state.entity = entity
        entity.distance_km = distance_from_user(geo, entity.lat, entity.lon)  # a looked-up place is measured from the traveller, or not at all
        destination = destination_by_id(entity.destination_id)
        tz = destination.timezone if destination else None
        if entity.opening_hours:
            status = opening_hours.status_at(entity.opening_hours, local_now(tz))
            entity.open_status = status["status"]
            entity.open_detail = {**entity.open_detail, **{k: v for k, v in status.items() if k != "status"}}
        state.candidates = [entity]
        state.evidence.add_candidate(entity, None, distance_from_user(geo, entity.lat, entity.lon))
        if geo.user_location is None and any(word in intent.raw_query.lower() for word in ("how far", "distance", "near")):
            state.extra.setdefault("closing_notes", []).append("Your current location is unavailable, so I can't tell how far it is from you.")
        if entity.destination_id and not entity.id.startswith("web-"):
            hits = retrieve(intent.raw_query, destination_id=entity.destination_id, poi_ids=[entity.id], limit=4).hits
            for hit in hits:
                if hit.poi_id == entity.id or hit.scores.get("bm25", 0) > 1.0:
                    state.evidence.add_knowledge(hit)
        if entity.destination_id:
            day = local_now(tz).date()
            for advisory in active_advisories(entity.destination_id, day, poi_ids=[entity.id]):
                if advisory.get("poi_id") == entity.id:
                    state.evidence.add_advisory(advisory)
        if intent.live_required and serp_client.configured:
            self._web_search(state, f"{entity.name} {entity.address or intent.locality_hint or ''} opening hours".strip(), limit=3)
        state.extra["lead"] = None

    def _route(self, state: "_State") -> None:
        intent, geo = state.intent, state.geo
        c = intent.constraints
        near = geo.user_point() or ((geo.active_destination.lat, geo.active_destination.lon) if geo.active_destination else None)
        destination, errors = resolve_place(c.route_destination, near=near)
        state.errors.extend(errors)
        if c.route_origin:
            origin_place, origin_errors = resolve_place(c.route_origin, near=near)
            state.errors.extend(origin_errors)
            origin = (origin_place.lat, origin_place.lon, origin_place.name) if origin_place else None
        elif geo.user_location:
            origin = (geo.user_location.lat, geo.user_location.lon, "your location")
        else:
            origin = None
        if destination is None or origin is None:
            missing = f"“{c.route_destination}”" if destination is None else (f"“{c.route_origin}”" if c.route_origin else "your starting point (turn on location)")
            state.extra["empty_message"] = f"I couldn't locate {missing}, so I can't work out what's on the way."
            state.needs = "route_endpoints"
            return
        result = route_suggestions(RouteRequest(origin=(origin[0], origin[1]), origin_label=origin[2], destination=(destination.lat, destination.lon), destination_label=destination.name, max_detour_min=c.max_travel_min, user=state.request.profile, constraints=c, category=intent.category), trace=state.trace)
        state.errors.extend(result["provider_errors"])
        state.candidates = result["items"]
        state.route = {"origin": origin[2], "destination": destination.name, "direct_km": result["direct_km"], "direct_min": result["direct_min"], "mode": result["mode"], "max_detour_min": result["max_detour_min"]}
        for candidate in result["items"][:6]:
            evidence = state.evidence.add_candidate(candidate, origin[2])
            evidence.content += f"; adds about {candidate.detour_min} min to the {result['direct_min']} min trip by {result['mode']} (estimate)"
        if not result["items"]:
            state.extra["empty_message"] = f"I didn't find verified places within a {result['max_detour_min']}-minute detour between {origin[2]} and {destination.name}."
        state.extra["lead"] = f"On the way from {origin[2]} to {destination.name} (~{result['direct_min']} min by {result['mode']}, estimate):"

    def _compare(self, state: "_State") -> None:
        geo = state.geo
        reference = geo.user_point() or ((geo.active_destination.lat, geo.active_destination.lon) if geo.active_destination else None)
        rows, unresolved = [], []
        for mention in state.intent.compare_mentions[:5]:
            resolution = resolve_entity(mention, reference=reference, trace=state.trace)
            if resolution.status == "resolved" and resolution.entity:
                rows.append(resolution.entity)
            else:
                unresolved.append(mention)
        if not rows:
            state.extra["empty_message"] = "I couldn't identify the places to compare. Try their full names."
            return
        ranked = rank(rows, RankRequest(profile_name="discovery", reference=reference, reference_label="you" if geo.user_point() else None, user=state.request.profile, local_time=local_now(_reference_timezone(geo)), time_sensitive=True, user_point=geo.user_point()))
        state.candidates = ranked.ranked
        for candidate in state.candidates:
            candidate.distance_km = distance_from_user(geo, candidate.lat, candidate.lon)
            state.evidence.add_candidate(candidate, "you" if candidate.distance_km is not None else None)
        state.comparison = {
            "columns": [c.name for c in state.candidates],
            "rows": [
                {"factor": "Distance", "values": [c.distance_km for c in state.candidates], "format": "km"},
                {"factor": "Rating", "values": [(f"{c.guest_score:.1f}/10" if c.guest_score is not None else f"{c.rating:.1f}/5") if (c.guest_score is not None or c.rating is not None) else None for c in state.candidates]},
                {"factor": "Cost", "values": [c.cost_for_user.get("display") for c in state.candidates]},
                {"factor": "Open now", "values": [{"open": "Yes", "closed": "No"}.get(c.open_status) for c in state.candidates]},
                {"factor": "Quietness", "values": [c.bars.get("quietness") for c in state.candidates], "format": "bar"},
                {"factor": "Visit", "values": [f"{c.visit_duration_min} min" if c.visit_duration_min else None for c in state.candidates]},
                {"factor": "Confidence", "values": [c.confidence_detail.get("label") for c in state.candidates]},
            ],
            "unresolved": unresolved,
        }
        state.extra["lead"] = "Here's how they compare (no single one is best for everyone):"
        if unresolved:
            state.extra.setdefault("closing_notes", []).append("I couldn't identify: " + ", ".join(unresolved) + ".")

    def _knowledge(self, state: "_State") -> None:
        geo, intent = state.geo, state.intent
        destination_id = geo.reference.destination_id if geo.reference else None
        if not destination_id and geo.user_location and geo.user_location.inside_destination_id:
            destination_id = geo.user_location.inside_destination_id
        if not destination_id and not self._require_reference(state):
            return
        label = geo.reference.label if geo.reference else "this area"
        state.trace.start_timer("knowledge")
        result = retrieve(_expanded_query(intent), destination_id=destination_id, limit=5) if destination_id else None
        state.trace.stop_timer("knowledge")
        if result:
            state.knowledge = [hit.as_dict() for hit in result.hits]
            state.trace.step("knowledge_retrieval", destination_id=destination_id, semantic_status=result.semantic_status, semantic_reason=result.semantic_reason, lexical_matches=result.lexical_matches, vector_matches=result.vector_matches, hits=[{"id": h.chunk_id, "scores": h.scores} for h in result.hits])
            state.semantic_status, state.semantic_reason = result.semantic_status, result.semantic_reason
            for hit in result.hits:
                state.evidence.add_knowledge(hit)
        weak = not result or not result.hits or max((h.scores.get("bm25", 0) for h in result.hits), default=0) < 1.0 and max((h.scores.get("vector", 0) for h in result.hits), default=0) < 0.45
        if weak and serp_client.configured and geo.reference:
            self._web_search(state, f"{intent.raw_query} {label}", limit=4)
        if not state.evidence.items:
            state.extra["empty_message"] = f"I don't have verified information about that for {label} yet." + ("" if serp_client.configured else " Web search is not configured.")

    def _itinerary(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        intent, reference = state.intent, state.geo.reference
        from app.services.plan_service import build_plan

        duration = None
        if intent.temporal and intent.temporal.duration_hours:
            duration = int(intent.temporal.duration_hours * 60)
        plan, weather, errors = build_plan(geo=state.geo, profile=state.request.profile, duration_min=duration, part_of_day=intent.temporal.part_of_day if intent.temporal else None, day_offset=_day_offset(intent), preset="balanced", wishes=intent.raw_query, constraints=intent.constraints, trace=state.trace)
        state.errors.extend(errors)
        state.plan = plan
        state.weather = weather
        if weather.get("status") == "ok":
            state.evidence.add_weather(weather, reference.label)
        stops = plan.get("stops") or []
        if not stops:
            state.extra["empty_message"] = f"I couldn't fit any verified stops into that time window around {reference.label}."
            return
        by_id = {c.id: c for c in plan.get("_candidates", [])}
        for stop in stops:
            candidate = by_id.get(stop["poi_id"])
            if candidate:
                evidence = state.evidence.add_candidate(candidate, reference.distance_label)
                evidence.content += f"; scheduled {stop['arrive']}–{stop['depart']} (travel {stop['leg']['minutes']} min by {stop['leg']['mode'].replace('_', ' ')}, estimate)"
        state.candidates = [by_id[s["poi_id"]] for s in stops if s["poi_id"] in by_id]
        state.extra["lead"] = f"Here's a {plan['used_min']}-minute plan starting {plan['start']} from {plan['start_label']}:"

    def _events(self, state: "_State") -> None:
        if not self._require_reference(state):
            return
        reference = state.geo.reference
        day = local_now(_reference_timezone(state.geo)).date()
        events = events_for(reference.destination_id, day, days=14)
        state.events = events
        for event in events[:5]:
            state.evidence.add_event(event)
        items, error = web_events(reference.label)
        if error and error.get("code") != "web_search_unavailable":
            state.errors.append(error)
        for item in items[:4]:
            state.evidence.add_web(item)
        if not serp_client.configured:
            state.extra.setdefault("closing_notes", []).append("Live event search is not configured, so only recurring events in the stored data are listed; confirm dates locally.")
        if not state.evidence.items:
            state.extra["empty_message"] = f"I have no verified events for {reference.label} in the coming days."

    def _web(self, state: "_State") -> None:
        label = state.geo.reference.label if state.geo.reference else ""
        if not serp_client.configured:
            state.extra["empty_message"] = "Web search is not configured on this server, so I can't look up current information."
            state.errors.append({"source": "serpapi", "code": "web_search_unavailable", "message": "SERPAPI_KEY is not set."})
            return
        self._web_search(state, f"{state.intent.raw_query} {label}".strip(), limit=5, freshness="month")

    def _web_search(self, state: "_State", query: str, limit: int = 4, freshness: str | None = None) -> None:
        try:
            response = serp_client.search(query, engine="google", limit=limit, freshness=freshness)
        except SearchProviderError as exc:
            state.errors.append(exc.as_dict())
            state.trace.error("serpapi", exc.code, exc.message)
            return
        items = [web_evidence(result, response.retrieved_at) for result in response.results if result.snippet]
        state.web = items
        for item in items[:limit]:
            state.evidence.add_web(item)
        state.trace.step("web_search", query=query, results=len(items), cached=response.cached)

    # ---- response ---------------------------------------------------------------

    def _respond(self, state: "_State") -> dict[str, Any]:
        context = AnswerContext(question=state.request.question, intent=state.intent.intent.value, location_notes=_location_notes(state.geo), evidence=state.evidence.items, extra=state.extra)
        state.trace.start_timer("generation")
        known = [c.name for c in state.candidates]
        answer = generate(context, language=state.request.language, trace=state.trace, known_names=known)
        state.trace.stop_timer("generation")
        state.errors.extend(answer.errors)
        coverage = answer.validation.evidence_coverage if answer.validation and state.evidence.items else 0.0
        confidence = 0.35 * state.intent.confidence + (0.4 if state.evidence.items else 0.0) + 0.25 * coverage
        if state.intent.intent == IntentType.PLACE_DISAMBIGUATION or state.needs:
            confidence = min(confidence, 0.4)
        # Adopt an explicitly named destination as the active one (Rule 3: context persists).
        next_destination = None
        query_destination = state.geo.query_destination
        if query_destination and query_destination.kind in {"destination", "geocoded"}:
            next_destination = query_destination.as_dict()
        elif query_destination and query_destination.destination_id:
            destination = destination_by_id(query_destination.destination_id)
            if destination:
                next_destination = destination_to_place(destination).as_dict()
        state.trace.set("evidence", [{"id": e.id, "type": e.source_type, "title": e.title} for e in state.evidence.items])
        state.trace.set("answer", {"mode": answer.mode, "validation": answer.validation.as_dict() if answer.validation else None})
        state.trace.emit()
        response = {
            "request_id": state.trace.request_id,
            "question": state.request.question,
            "answer": answer.text,
            "answer_meta": answer.as_dict(),
            "intent": state.intent.as_dict(),
            "geo_context": state.geo.as_dict(),
            "next_active_destination": next_destination,
            "needs": state.needs,
            "results": [candidate.as_dict() for candidate in state.candidates[:10]],
            "entity_resolution": state.resolution,
            "clarification": state.clarification,
            "knowledge": state.knowledge,
            "weather": state.weather,
            "safety": state.safety,
            "events": state.events,
            "plan": {k: v for k, v in state.plan.items() if not k.startswith("_")} if state.plan else None,
            "route": state.route,
            "comparison": state.comparison,
            "understood": intent_constraints_understood(state.intent),
            "sources": [item.as_dict() for item in state.evidence.items],
            "confidence": round(confidence, 2),
            "notices": _notices(state),
            "provider_errors": state.errors,
        }
        if state.request.debug:
            response["debug"] = state.trace.as_dict()
        return response


def intent_constraints_understood(intent: QueryIntent) -> list[str]:
    return list(intent.constraints.understood) if intent.constraints is not None else []


def _notices(state: "_State") -> list[str]:
    notices = list(state.geo.warnings)
    for candidate in state.candidates[:6]:
        for conflict in candidate.conflicts:
            notices.append(f"Information about {candidate.name} is inconsistent ({conflict['detail']}). Verify before travelling.")
    codes = {error.get("code") for error in state.errors}
    if "web_search_unavailable" in codes:
        notices.append("Live web search is not configured; results come from stored data.")
    if any(error.get("source") == "groq" for error in state.errors):
        notices.append("The language model was unavailable, so this answer was assembled directly from verified data.")
    if state.semantic_status and state.semantic_status != "ok":
        loading = "loading" in (state.semantic_reason or "")
        notices.append("Semantic search is warming up; knowledge results use keyword matching for now." if loading else "Semantic search is unavailable; knowledge results use keyword matching.")
    return list(dict.fromkeys(notices))


@dataclass
class _State:
    request: AskRequest
    intent: QueryIntent
    geo: GeoContext
    trace: Trace
    evidence: EvidenceBuilder = field(default_factory=EvidenceBuilder)
    candidates: list[Candidate] = field(default_factory=list)
    entity: Candidate | None = None
    resolution: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    weather: dict[str, Any] | None = None
    safety: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    web: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    needs: str | None = None
    semantic_status: str | None = None
    semantic_reason: str | None = None


query_service = QueryService()
