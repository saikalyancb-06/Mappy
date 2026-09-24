"""Optional LLM refinement of query understanding.

Only used when the rule parser is unsure. The LLM's output is validated
against the intent enum and the taxonomy; it can fill gaps but it cannot
invent categories, coordinates or places.
"""
from __future__ import annotations

import json

from app.config import LLM_QUERY_PARSING
from app.core.rules import taxonomy
from app.llm.client import LLMClient, LLMUnavailable, llm as default_llm
from app.query.models import IntentType, QueryIntent
from app.query.parser import apply_requirements

_SYSTEM = (
    "You classify travel questions for a location-aware travel app. Return JSON with keys: "
    "intent (one of {intents}), category (one of {categories} or null), entity (a specific named place or business the user asks about, or null), "
    "place (a destination/area named after in/at/near, or null), near_me (true if the user means their own current location), "
    "preferences (subset of {preferences}). Use null when unsure. Do not invent names that are not in the question."
)


def refine_with_llm(intent: QueryIntent, client: LLMClient | None = None) -> QueryIntent:
    client = client or default_llm
    if not LLM_QUERY_PARSING or not client.configured or intent.confidence >= 0.6:
        return intent
    system = _SYSTEM.format(intents=[i.value for i in IntentType], categories=sorted(taxonomy()["categories"]), preferences=sorted(taxonomy()["preferences"]))
    try:
        data = client.chat_json([{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"question": intent.raw_query})}])
    except LLMUnavailable:
        return intent
    raw_lower = intent.raw_query.lower()
    try:
        proposed = IntentType(str(data.get("intent")))
    except ValueError:
        return intent
    intent.intent = proposed
    category = data.get("category")
    if category in taxonomy()["categories"] and not intent.category:
        intent.category = category
    for key, attr in (("entity", "entity_mention"), ("place", "place_mention")):
        value = data.get(key)
        # Accept only text that literally appears in the question.
        if isinstance(value, str) and value.strip() and value.lower() in raw_lower and not getattr(intent, attr):
            setattr(intent, attr, value.strip())
    if data.get("near_me") is True and not intent.spatial_relation:
        intent.spatial_relation = "near_me"
    for preference in data.get("preferences") or []:
        if preference in taxonomy()["preferences"] and preference not in intent.preferences:
            intent.preferences.append(preference)
    intent.parser = "rules+llm"
    intent.confidence = 0.7
    apply_requirements(intent, time_sensitive=bool(intent.temporal and intent.temporal.label in {"now", "today", "tonight", "tomorrow"}))
    return intent
