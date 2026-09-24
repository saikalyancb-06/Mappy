"""What kind of events a request asks for, read from its words (vocabulary in events.json)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.rules import load_rules
from app.core.text import normalize

_GENERIC = {"event", "events", "happening", "things", "stuff", "activities"}


@dataclass
class EventIntent:
    categories: list[str] = field(default_factory=list)
    free_only: bool = False
    festival_only: bool = False
    near_me: bool = False


def event_intent(text: str | None) -> EventIntent:
    rules = load_rules("events")
    norm = f" {normalize(text or '')} "
    intent = EventIntent()
    for category in rules["category_priority"]:
        words = [w for w in rules["categories"][category]["keywords"] if normalize(w) not in _GENERIC]
        if any(f" {normalize(w)} " in norm for w in words):
            intent.categories.append(category)
    if re.search(r"\b(?:festival|festivals|fest|utsav|celebration|celebrations)\b", norm) or any(f" {normalize(w)} " in norm for w in rules["festival_keywords"]):
        intent.festival_only = True
        intent.categories = [c for c in intent.categories if c not in {"festivals", "religious"}]
    intent.free_only = bool(re.search(r"\bfree\b(?!\s+(?:from|time|slot))", norm))
    intent.near_me = bool(re.search(r"\b(?:near me|around me|nearby|close to me|near here)\b", norm))
    return intent
