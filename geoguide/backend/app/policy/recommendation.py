"""The single, deterministic recommendation policy.

Every path that *suggests* places (ranking → discovery, nearby, plans and
itineraries, hotels, search results; city place lists; knowledge retrieval)
calls ``is_recommendation_excluded``. It is data-driven
(``data/config/recommendation_policy.json``) and applied in code, not left to an
LLM instruction.

Rules, all neutral with respect to religion:

* places that are permanently closed are never suggested;
* categories / provider types a deployment lists are never suggested;
* when the traveller turns on ``exclude_places_of_worship``, places of worship
  of every faith are left out of suggestions.

Explicit factual questions about a named place are not recommendations and are
answered normally (callers pass ``context.explicit=True``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.rules import load_rules
from app.core.text import normalize


@dataclass
class PolicyContext:
    explicit: bool = False  # the traveller asked about this specific place
    exclude_places_of_worship: bool = False  # traveller preference, applies to all faiths
    extra_categories: set[str] = field(default_factory=set)


def _field(poi: Any, name: str) -> Any:
    return poi.get(name) if isinstance(poi, dict) else getattr(poi, name, None)


def _types(poi: Any) -> list[str]:
    raw = _field(poi, "provider_types") or []
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [raw]
    extra = [_field(poi, "subcategory")]
    return [normalize(str(t)) for t in [*raw, *extra] if t]


def is_place_of_worship(poi: Any) -> bool:
    rules = load_rules("recommendation_policy")["place_of_worship"]
    if _field(poi, "category") in set(rules["categories"]):
        return True
    terms = [normalize(t) for t in rules["provider_type_terms"]]
    return any(f" {term} " in f" {kind} " for kind in _types(poi) for term in terms)


def exclusion_reason(poi: Any, context: PolicyContext | None = None) -> str | None:
    context = context or PolicyContext()
    rules = load_rules("recommendation_policy")
    if context.explicit:
        return None
    status = _field(poi, "status")
    detail = _field(poi, "open_detail") or {}
    if status in set(rules["never_recommend_statuses"]) or (isinstance(detail, dict) and detail.get("permanently_closed")):
        return "permanently_closed"
    category = _field(poi, "category")
    if category and (category in set(rules["excluded_categories"]) or category in context.extra_categories):
        return "excluded_category"
    blocked = [normalize(t) for t in rules["excluded_provider_types"]]
    if blocked and any(f" {b} " in f" {kind} " for kind in _types(poi) for b in blocked):
        return "excluded_provider_type"
    if context.exclude_places_of_worship and is_place_of_worship(poi):
        return "place_of_worship_excluded_by_traveller"
    return None


def is_recommendation_excluded(poi: Any, context: PolicyContext | None = None) -> bool:
    return exclusion_reason(poi, context) is not None


def context_from_profile(profile: dict[str, Any] | None, *, explicit: bool = False) -> PolicyContext:
    profile = profile or {}
    return PolicyContext(explicit=explicit, exclude_places_of_worship=bool(profile.get("exclude_places_of_worship")))
