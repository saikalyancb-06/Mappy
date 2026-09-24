"""Query routing: answer from the city dataset when it can, go live only when needed.

    city knowledge          → database + vector retrieval (RAG)
    places / recommendations → database; live Google Maps (SerpApi) when the city
                               isn't prepared, stored coverage is thin, or a place is unknown
    current events           → event pipeline (always live, short cache)
    current information      → web search
    weather                  → weather provider
    mixed ("peaceful places near me that are open now") → hybrid: stored places +
                               position + stored hours evaluated now + vibe profile

Simple structured filters (category, distance, open now, price) come from the
rule parser; no LLM call is needed to route.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.rules import load_rules
from app.db.models import Destination
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
PREPARED = {"READY", "PARTIAL", "STALE"}


@dataclass
class RouteDecision:
    route: str  # database | live_places | hybrid
    reason: str
    allow_live: bool
    persist_live: bool = False
    city_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def city_status(destination_id: str | None) -> str | None:
    if not destination_id:
        return None
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
    return (destination.enrichment_status or "NOT_STARTED") if destination else None


def route_places(*, destination_id: str | None, stored_count: int, category_requested: bool, time_sensitive: bool, live_available: bool, legacy_minimum: int) -> RouteDecision:
    """Decide how a place question is answered, given what the database already holds."""
    rules = load_rules("city_intelligence")["routing"]
    status = city_status(destination_id)
    prepared = status in PREPARED
    needed = (rules["min_category_results"] if category_requested else rules["min_db_results"]) if prepared else legacy_minimum
    enough = stored_count >= needed
    details = {"stored": stored_count, "needed": needed}
    if enough:
        decision = RouteDecision("hybrid" if time_sensitive else "database", "stored places evaluated against the current time" if time_sensitive else "enough stored places", False, city_status=status, details=details)
    elif live_available:
        why = "city not prepared yet" if not prepared else "too few stored places for this request"
        decision = RouteDecision("live_places", why, True, persist_live=destination_id is not None, city_status=status, details=details)
    else:
        decision = RouteDecision("database", "live place search is not configured", False, city_status=status, details=details)
    event = {"database": "database_answer", "hybrid": "hybrid_answer", "live_places": "live_search_triggered"}[decision.route]
    logger.info("%s destination=%s status=%s stored=%d needed=%d reason=%s", event, destination_id, status, stored_count, needed, decision.reason)
    return decision


def route_question(intent: str, *, live_required: bool = False, events_required: bool = False) -> str:
    """Coarse route for a parsed question (used for traces and the API's routing info)."""
    if intent == "WEATHER":
        return "weather"
    if intent == "LIVE_INFORMATION" or events_required:
        return "events"
    if intent == "WEB_RESEARCH":
        return "web"
    if intent in {"DESTINATION_KNOWLEDGE", "GENERAL_TRAVEL_QUESTION", "SAFETY"}:
        return "knowledge"
    if live_required:
        return "hybrid"
    return "places"
