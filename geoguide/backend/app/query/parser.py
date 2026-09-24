"""Rule-based query understanding.

Turns a natural-language question into a structured ``QueryIntent`` using the
taxonomy and cue lexicons in data/config. Nothing here knows about specific
places or businesses: entity and place *mentions* are extracted as text and
resolved later against data.
"""
from __future__ import annotations

import re

from app.core.rules import load_rules, taxonomy
from app.core.text import has_phrase, normalize
from app.query.constraints import parse_constraints
from app.query.models import IntentType, QueryIntent, TemporalConstraint

_ENTITY_PATTERNS = [
    r"\bwhere\s+(?:is|are|'s)\s+(?P<e>.+)",
    r"\bhow\s+(?:far|long)\s+(?:is|to|away\s+is)\s+(?P<e>.+)",
    r"\bhow\s+(?:do|can|should)\s+i\s+(?:get|go|reach)\s+to\s+(?P<e>.+)",
    r"\bdirections?\s+to\s+(?P<e>.+)",
    r"\bwhen\s+(?:does|do|is)\s+(?P<e>.+?)\s+(?:open|close|shut)\b",
    r"\bis\s+(?P<e>.+?)\s+(?:open|closed|crowded|busy|worth|good|safe|free|accessible|wheelchair|suitable|ok|okay)\b",
    r"\bdoes\s+(?P<e>.+?)\s+(?:have|charge|allow|serve|open|close)\b",
    r"\b(?:timings?|opening\s+hours|hours|entry\s+fees?|ticket\s+prices?|tickets?|dress\s+code)\s+(?:for|of|at|to)\s+(?P<e>.+)",
    r"\btell\s+me\s+(?:more\s+)?about\s+(?P<e>.+)",
    r"\bwhat\s+is\s+(?P<e>.+?)\s+(?:famous|known)\s+for\b",
    r"\b(?:history|significance|story|importance|architecture|legend)\s+(?:of|behind)\s+(?P<e>.+)",
    r"\bwhy\s+is\s+(?P<e>.+?)\s+(?:famous|important|significant|popular|special)\b",
    r"\bwho\s+built\s+(?P<e>.+)",
]
_ENTITY_CUT = re.compile(r"\s+(?:in|near|at|around|from|open|today|tonight|tomorrow|now|right\s+now|for|on|with|by|and|if|when|during|currently)\b.*$|[?.!,;].*$", re.IGNORECASE)
# Lookahead so overlapping phrases ("to eat near X") are all considered.
_PREPOSITIONS = re.compile(r"(?=\b(?P<prep>in|at|around|near|nearby|of|to|towards)\s+(?P<phrase>[^?,.!;]+))", re.IGNORECASE)
_RADIUS = re.compile(r"\b(?:within|in|under|less\s+than|up\s+to)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>km|kms|kilometers|kilometres|k|m|meters|metres|mi|miles)\b", re.IGNORECASE)
_DURATION = re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*(?:hours|hrs|hour|hr|h)\b", re.IGNORECASE)
_PRONOUN_SUBJECT = re.compile(r"^\s*(?:is|was|does|will|can|how|what\s+about)\s+(?:it|this|that|this\s+place|that\s+place)\b(?!\s+(?:is|are)\b)", re.IGNORECASE)


def _cue(norm: str, phrases: list[str]) -> str | None:
    for phrase in sorted(phrases, key=len, reverse=True):
        target = normalize(phrase)
        if target and has_phrase(norm, target):
            return phrase
    return None


def _all_cues(norm: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if has_phrase(norm, phrase)]


def _vocabulary_words() -> set[str]:
    rules = load_rules("intents")
    words: set[str] = set()
    for phrases in rules["cues"].values():
        for phrase in phrases:
            words.update(normalize(phrase).split())
    for entry in taxonomy()["categories"].values():
        for phrase in entry["synonyms"]:
            words.update(normalize(phrase).split())
    for phrases in taxonomy()["preferences"].values():
        for phrase in phrases:
            words.update(normalize(phrase).split())
    words.update(normalize(" ".join(rules["determiners"] + rules["stop_words"] + rules["self_references"])).split())
    words.update({"minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs", "km", "kms", "m", "metres", "meters", "rs", "rupees", "inr", "pm", "am", "only", "places", "options"})
    words.update({"night", "morning", "evening", "afternoon", "week", "weekend", "day", "days", "hour", "hours", "moment"})
    words.update({"place", "places", "thing", "things", "spot", "spots", "area", "areas", "somewhere", "something", "anything", "one", "ones", "some", "what", "where", "which", "i", "we", "you", "can", "should", "do", "go", "see", "get"})
    words.update({"city", "town", "destination", "like", "region"})  # "this city" / "the town" mean the current destination, not a named place
    return words


def _is_meaningful_mention(phrase: str) -> bool:
    words = normalize(phrase).split()
    if not words:
        return False
    vocabulary = _vocabulary_words()
    return any(word not in vocabulary and not word.isdigit() for word in words)


def _cue_words() -> set[str]:
    words: set[str] = set()
    for phrases in load_rules("intents")["cues"].values():
        for phrase in phrases:
            words.update(normalize(phrase).split())
    return words


def _clean_mention(phrase: str) -> str:
    rules = load_rules("intents")
    phrase = _ENTITY_CUT.sub("", phrase.strip())
    words = phrase.strip(" '\"").split()
    while words and words[0].lower() in rules["determiners"]:
        words = words[1:]
    cue_words = _cue_words()
    # Drop trailing qualifiers ("<place> historically") that describe the question, not the place.
    while len(words) > 1 and normalize(words[-1]) in cue_words:
        words = words[:-1]
    return " ".join(words).strip(" '\"")


def _is_self_reference(phrase: str) -> bool:
    norm = normalize(phrase)
    return norm in {normalize(p) for p in load_rules("intents")["self_references"]}


def _is_pronoun(phrase: str) -> bool:
    norm = normalize(phrase)
    return norm in {normalize(p) for p in load_rules("intents")["pronoun_references"]} or norm in {"this place", "that place"}


def _temporal(norm: str) -> TemporalConstraint | None:
    part = None
    for label, pattern in (("morning", r"\bmorning\b"), ("afternoon", r"\bafternoon\b"), ("evening", r"\bevening\b|\bsunset\b"), ("night", r"\bnight\b")):
        if re.search(pattern, norm):
            part = label
            break
    duration = None
    match = _DURATION.search(norm)
    if match:
        duration = float(match.group("value"))
    elif re.search(r"\bhalf\s+day\b", norm):
        duration = 4.0
    elif re.search(r"\b(?:full|whole|one|entire)\s+day\b", norm):
        duration = 9.0
    if re.search(r"\btomorrow\b", norm):
        return TemporalConstraint("tomorrow", 1, part, duration)
    if re.search(r"\btonight\b", norm):
        return TemporalConstraint("tonight", 0, "evening", duration)
    if re.search(r"\bweekend\b", norm):
        return TemporalConstraint("weekend", 0, part, duration)
    if re.search(r"\btoday\b|\bthis\s+(?:morning|afternoon|evening)\b", norm):
        return TemporalConstraint("today", 0, part, duration)
    if re.search(r"\b(?:now|right\s+now|currently|at\s+the\s+moment)\b", norm):
        return TemporalConstraint("now", 0, part, duration)
    if part:
        return TemporalConstraint("today", 0, part, duration)
    if duration:
        return TemporalConstraint("duration", 0, None, duration)
    return None


def _radius(text: str, norm: str) -> float | None:
    rules = load_rules("intents")
    match = _RADIUS.search(text)
    if match:
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        km = value / 1000 if unit in {"m", "meters", "metres"} else value * 1.609 if unit in {"mi", "miles"} else value
        return max(0.1, min(km, float(rules["max_radius_km"])))
    if re.search(r"\bwalking\s+distance\b|\bwalkable\b|\bwalk\s+to\b", norm):
        return float(rules["walking_distance_km"])
    return None


def _category(norm: str) -> tuple[str | None, str | None]:
    best: tuple[int, str] | None = None
    for category_id, entry in taxonomy()["categories"].items():
        for synonym in entry["synonyms"]:
            target = normalize(synonym)
            if target and has_phrase(norm, target):
                if best is None or len(target) > best[0]:
                    best = (len(target), category_id)
    group = None
    for group_id, entry in taxonomy()["groups"].items():
        if has_phrase(norm, entry["label"]) or has_phrase(norm, group_id):
            group = group_id
            break
    return (best[1] if best else None), group


def _preferences(norm: str) -> list[str]:
    """Longest phrase wins and consumes its words ("step free" is not also "free")."""
    phrases = sorted(((normalize(phrase), preference) for preference, items in taxonomy()["preferences"].items() for phrase in items), key=lambda item: len(item[0]), reverse=True)
    remaining = f" {norm} "
    found: list[str] = []
    for phrase, preference in phrases:
        pattern = rf"(?<=\s){re.escape(phrase)}(?=\s)"
        if phrase and re.search(pattern, remaining):
            remaining = re.sub(pattern, " ", remaining)
            if preference not in found:
                found.append(preference)
    return found


def _entity(text: str) -> tuple[str | None, bool]:
    """Return (entity mention, refers_to_pronoun)."""
    if _PRONOUN_SUBJECT.search(text):
        return None, True
    for pattern in _ENTITY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        mention = _clean_mention(match.group("e"))
        if not mention:
            continue
        if _is_pronoun(mention):
            return None, True
        if _is_self_reference(mention) or not _is_meaningful_mention(mention) or _CURRENT_CITY.search(normalize(mention)):
            continue
        return mention, False
    return None, False


# "the history of this city", "food in the city", "this town": about the current destination, not a named place.
_CURRENT_CITY = re.compile(r"(?:^|\s)(?:this|the|our|my) (?:city|town|place|area|destination|region)$|^(?:this|the) (?:city|town)\b")


def _is_date_phrase(phrase: str) -> bool:
    """'October', 'Oct 22', '22 October 2026' are dates, not places."""
    from datetime import date

    from app.core.dates import MONTHS, parse_day

    words = normalize(phrase).split()
    return bool(words) and (words[0] in MONTHS and (len(words) == 1 or words[1].isdigit()) or parse_day(phrase, date.today()) is not None)


def _place_mentions(text: str) -> tuple[str | None, str | None, bool]:
    """Return (place mention, relation, near_me) from prepositional phrases."""
    rules = load_rules("intents")
    stop = {w.lower() for w in rules["stop_words"]}
    found: list[tuple[int, str, str]] = []
    near_me = False
    for match in _PREPOSITIONS.finditer(text):
        prep = match.group("prep").lower()
        words = []
        for word in match.group("phrase").split():
            if word.lower().strip("'\"") in stop and words:
                break
            if word.lower() in stop and not words and word.lower() not in rules["determiners"]:
                break
            words.append(word)
        raw_phrase = " ".join(words)
        if _is_self_reference(raw_phrase):
            near_me = True
            continue
        phrase = _clean_mention(raw_phrase)
        if not phrase or _is_date_phrase(phrase):
            continue
        if _is_self_reference(phrase) or normalize(phrase) in {"me", "us"}:
            near_me = True
            continue
        if _is_pronoun(phrase) or not _is_meaningful_mention(phrase):
            continue
        priority = 0 if prep in {"in", "at", "around", "near", "nearby"} else 1
        relation = "near_place" if prep in {"near", "nearby", "around", "towards"} else "in_place"
        found.append((priority, phrase, relation))
    if not found:
        return None, None, near_me
    found.sort(key=lambda item: item[0])
    return found[0][1], found[0][2], near_me


_COMPARE = re.compile(r"\bcompare\s+(?P<list>.+)|(?P<a>[\w' .&-]+?)\s+(?:vs\.?|versus)\s+(?P<b>[\w' .&-]+)", re.IGNORECASE)


def _compare_mentions(text: str) -> list[str]:
    match = _COMPARE.search(text)
    if not match:
        return []
    if match.group("list"):
        body = re.sub(r"\b(?:for me|please|places|these|the)\b", " ", match.group("list"), flags=re.IGNORECASE)
        parts = [p.strip(" ?.!") for p in re.split(r",|\band\b|\bwith\b|\bvs\.?\b|\bversus\b|&", body, flags=re.IGNORECASE)]
    else:
        parts = [match.group("a").strip(" ?.!"), match.group("b").strip(" ?.!")]
    parts = [p for p in parts if p and _is_meaningful_mention(p)]
    return parts if len(parts) >= 2 else []


def parse_query(text: str, *, has_selected_entity: bool = False) -> QueryIntent:
    rules = load_rules("intents")
    cues = rules["cues"]
    raw = " ".join((text or "").split())
    norm = normalize(raw)
    signals: list[str] = []

    temporal = _temporal(norm)
    radius = _radius(raw, norm)
    entity, pronoun = _entity(raw)
    place, relation, near_me = _place_mentions(raw)
    if re.search(r"(?:^|\s)(?:nearby|near by|close by|around here|in my area|walking distance|near me)(?:\s|$)", norm):
        near_me = True
    if not place and not entity and re.search(r"(?:^|\s)there(?:\s|$)", norm) and not re.search(r"(?:^|\s)(?:is|are)\s+there(?:\s|$)", norm):
        pronoun = True
    if entity and place and normalize(place) in normalize(entity):
        place, relation = None, None  # the place phrase was part of the entity name
    if entity and place and normalize(entity) == normalize(place):
        place = None

    # Detect categories on the text with named places removed ("<name> Hotel", "<name> Bazaar"
    # name places, they do not ask for hotels or markets).
    category_text = norm
    for mention in (entity, place):
        if mention:
            category_text = f" {category_text} ".replace(f" {normalize(mention)} ", " ").strip()
    category, group = _category(category_text)
    preferences = _preferences(norm)

    has = {name: _cue(norm, phrases) for name, phrases in cues.items()}
    for name, hit in has.items():
        if hit:
            signals.append(f"cue:{name}={hit}")

    if near_me:
        spatial = "near_me"
    elif place:
        spatial = relation
    elif pronoun and not entity:
        spatial = "pronoun"
    elif radius is not None:
        spatial = "radius"
    else:
        spatial = None

    time_sensitive = temporal is not None and temporal.label in {"now", "today", "tonight", "tomorrow", "weekend"}

    intent: IntentType
    confidence = 0.9
    if pronoun and has_selected_entity and not has["weather"]:
        intent = IntentType.PLACE_LOOKUP
        signals.append("rule:pronoun_selected_entity")
    elif has["weather"] and not (has["discovery"] or has["itinerary"] or category or group):
        intent = IntentType.WEATHER
    elif has["safety"] and not category:
        intent = IntentType.SAFETY
    elif has["itinerary"] or (temporal and temporal.duration_hours and not category):
        intent = IntentType.ITINERARY
    elif has["events"] and not entity:
        intent = IntentType.LIVE_INFORMATION
    elif entity:
        intent = IntentType.PLACE_LOOKUP
    elif category and has["knowledge"] and not (spatial or has["quality"] or has["discovery"]):
        intent = IntentType.DESTINATION_KNOWLEDGE  # "how should I dress for temples?"
    elif category:
        intent = IntentType.NEARBY_SEARCH
    elif pronoun and not place and not has["discovery"]:
        intent = IntentType.DESTINATION_KNOWLEDGE  # "is this place good for a family?"
    elif has["web"]:
        intent = IntentType.WEB_RESEARCH
    elif has["discovery"] or group or (preferences and (near_me or place)):
        intent = IntentType.ACTIVITY_DISCOVERY
    elif near_me or radius is not None or preferences:
        intent = IntentType.ACTIVITY_DISCOVERY
    elif has["live"]:
        intent = IntentType.ACTIVITY_DISCOVERY  # "what is still open?"
        temporal = temporal or TemporalConstraint("now")
    elif has["knowledge"] or pronoun:
        intent = IntentType.DESTINATION_KNOWLEDGE
    elif place:
        intent = IntentType.DESTINATION_KNOWLEDGE
        confidence = 0.7
    else:
        intent = IntentType.GENERAL_TRAVEL_QUESTION
        confidence = 0.4

    constraints = parse_constraints(raw)
    compare = _compare_mentions(raw)
    if compare:
        intent, confidence = IntentType.COMPARE, 0.85
    elif constraints.route_destination:
        intent, confidence = IntentType.ROUTE_SUGGESTIONS, 0.85
        category = constraints.include_categories[0] if constraints.include_categories else None  # not a word from the endpoints' names
    elif (constraints.window_start and constraints.window_end) and intent not in {IntentType.WEATHER, IntentType.SAFETY}:
        intent = IntentType.ITINERARY
    elif constraints.available_minutes and intent in {IntentType.ACTIVITY_DISCOVERY, IntentType.GENERAL_TRAVEL_QUESTION, IntentType.DESTINATION_KNOWLEDGE} and not category:
        intent = IntentType.ITINERARY
    elif intent in {IntentType.GENERAL_TRAVEL_QUESTION, IntentType.DESTINATION_KNOWLEDGE, IntentType.WEB_RESEARCH} and not constraints.is_empty and (constraints.max_travel_min or constraints.max_cost or constraints.max_price_level or constraints.min_rating or constraints.ranking_mode or constraints.open_now or constraints.limit):
        intent = IntentType.ACTIVITY_DISCOVERY
    if constraints.open_now and temporal is None:
        temporal = TemporalConstraint("now")
    for preference in constraints.preferences:
        if preference not in preferences:
            preferences.append(preference)
    result = QueryIntent(
        intent=intent,
        raw_query=raw,
        category=category,
        category_group=group,
        entity_mention=entity,
        locality_hint=place if entity else None,
        place_mention=place,
        spatial_relation=spatial,
        radius_km=radius,
        temporal=temporal,
        preferences=preferences,
        quality_focus=bool(has["quality"]),
        confidence=confidence,
        signals=signals,
        constraints=constraints,
        compare_mentions=compare,
    )
    apply_requirements(result, live_cue=bool(has["live"]), weather_cue=bool(has["weather"]), time_sensitive=time_sensitive, knowledge_cue=bool(has["knowledge"]))
    return result


def apply_requirements(intent: QueryIntent, *, live_cue: bool = False, weather_cue: bool = False, time_sensitive: bool = False, knowledge_cue: bool = False) -> None:
    """Derive which evidence classes an intent needs."""
    kind = intent.intent
    intent.weather_required = weather_cue or kind in {IntentType.WEATHER, IntentType.ITINERARY, IntentType.SAFETY} or (kind in {IntentType.ACTIVITY_DISCOVERY, IntentType.RECOMMENDATION} and time_sensitive)
    intent.safety_required = kind in {IntentType.SAFETY, IntentType.ITINERARY, IntentType.ACTIVITY_DISCOVERY, IntentType.RECOMMENDATION}
    intent.events_required = kind == IntentType.LIVE_INFORMATION or (kind in {IntentType.ACTIVITY_DISCOVERY, IntentType.RECOMMENDATION} and time_sensitive)
    intent.knowledge_required = knowledge_cue or kind in {IntentType.DESTINATION_KNOWLEDGE, IntentType.PLACE_LOOKUP, IntentType.ACTIVITY_DISCOVERY, IntentType.RECOMMENDATION, IntentType.GENERAL_TRAVEL_QUESTION}
    intent.live_required = live_cue or kind == IntentType.LIVE_INFORMATION
    intent.web_required = kind in {IntentType.WEB_RESEARCH, IntentType.LIVE_INFORMATION} or (kind == IntentType.PLACE_LOOKUP and live_cue)
