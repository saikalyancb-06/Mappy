"""Query understanding is evaluated on a labelled set covering each intent class.

The set deliberately uses many different place and business names so passing
it cannot come from special-casing any one example.
"""
import pytest

from app.query.models import IntentType
from app.query.parser import parse_query

LABELLED = [
    # (query, intent, category, spatial_relation)
    ("Coffee shops near me", "NEARBY_SEARCH", "cafe", "near_me"),
    ("best cafes nearby", "NEARBY_SEARCH", "cafe", "near_me"),
    ("restaurants near me", "NEARBY_SEARCH", "restaurant", "near_me"),
    ("any pharmacy close by?", "NEARBY_SEARCH", "pharmacy", "near_me"),
    ("hotels in Rivertown", "NEARBY_SEARCH", "hotel", "in_place"),
    ("places to eat near Clock Square", "NEARBY_SEARCH", "restaurant", "near_place"),
    ("Where can I get good South Indian food?", "NEARBY_SEARCH", "restaurant", None),
    ("What can I do around here?", "ACTIVITY_DISCOVERY", None, "near_me"),
    ("What should I visit in Portbury?", "ACTIVITY_DISCOVERY", None, "in_place"),
    ("things to do in Lakeside tomorrow", "ACTIVITY_DISCOVERY", None, "in_place"),
    ("What can I do within 5 km?", "ACTIVITY_DISCOVERY", None, "radius"),
    ("Find peaceful places near me", "ACTIVITY_DISCOVERY", None, "near_me"),
    ("What are the best things to do tonight?", "ACTIVITY_DISCOVERY", None, None),
    ("which places are step-free?", "ACTIVITY_DISCOVERY", None, None),
    ("Where is Blue Door Bakery in Old Quarter?", "PLACE_LOOKUP", None, None),
    ("How far is the Grand Mosque?", "PLACE_LOOKUP", None, None),
    ("Is Harbour Museum open now?", "PLACE_LOOKUP", None, None),
    ("What is the weather there?", "WEATHER", None, "pronoun"),
    ("Is it raining near me?", "WEATHER", None, "near_me"),
    ("Weather in Northfield tomorrow", "WEATHER", None, "in_place"),
    ("Is Portbury safe at night?", "SAFETY", None, None),
    ("Plan my evening around Lakeside", "ITINERARY", None, "in_place"),
    ("plan 4 hours in Rivertown", "ITINERARY", None, "in_place"),
    ("Any festivals this week in Portbury?", "LIVE_INFORMATION", None, "in_place"),
    ("What is the history of Oldham Castle?", "PLACE_LOOKUP", None, None),
    ("how should I dress for temples?", "DESTINATION_KNOWLEDGE", "temple", None),
    ("Is this place good for a family?", "DESTINATION_KNOWLEDGE", None, "pronoun"),
    ("latest news about the old bridge", "WEB_RESEARCH", None, None),
]


def test_intent_accuracy_on_labelled_set():
    correct = 0
    failures = []
    for query, intent, category, relation in LABELLED:
        parsed = parse_query(query)
        ok = parsed.intent.value == intent and parsed.category == category and (relation is None or parsed.spatial_relation == relation)
        correct += ok
        if not ok:
            failures.append((query, parsed.intent.value, parsed.category, parsed.spatial_relation))
    accuracy = correct / len(LABELLED)
    assert accuracy >= 0.95, failures


@pytest.mark.parametrize("query,entity,locality", [
    ("Where is SLV Hotel in Gandhi Bazaar?", "SLV Hotel", "Gandhi Bazaar"),
    ("Where is Blue Door Bakery in Old Quarter?", "Blue Door Bakery", "Old Quarter"),
    ("Is Harbour Museum open now?", "Harbour Museum", None),
    ("how do I get to the Stone Bridge", "Stone Bridge", None),
])
def test_entity_and_locality_extraction(query, entity, locality):
    parsed = parse_query(query)
    assert parsed.entity_mention == entity
    assert parsed.locality_hint == locality


def test_named_places_do_not_leak_into_category():
    # "Hotel" in a business name and "Bazaar" in a locality must not become category searches.
    parsed = parse_query("Where is Moonlight Hotel in Flower Bazaar?")
    assert parsed.category is None
    assert parse_query("restaurants near Fish Market").place_mention == "Fish Market"


def test_self_references_are_never_destinations():
    for query in ("cafes near me", "what is around here", "restaurants near my location"):
        parsed = parse_query(query)
        assert parsed.place_mention is None
        assert parsed.spatial_relation == "near_me"


def test_temporal_and_radius_constraints():
    parsed = parse_query("museums within 2.5 km tomorrow morning")
    assert parsed.radius_km == 2.5
    assert parsed.temporal.label == "tomorrow" and parsed.temporal.day_offset == 1 and parsed.temporal.part_of_day == "morning"
    assert parse_query("cafes within 800 m").radius_km == pytest.approx(0.8)
    assert parse_query("plan a half day").temporal.duration_hours == 4.0


def test_preferences_longest_phrase_wins():
    assert parse_query("step-free places please").preferences == ["step_free"]
    assert "family" in parse_query("kid friendly things to do").preferences


def test_requirements_follow_intent():
    nearby = parse_query("coffee near me")
    assert not nearby.weather_required and not nearby.knowledge_required and not nearby.web_required
    discovery = parse_query("what should I do tomorrow")
    assert discovery.weather_required and discovery.knowledge_required
    weather = parse_query("is it raining near me")
    assert weather.intent == IntentType.WEATHER and not weather.knowledge_required


def test_selected_place_pronoun_becomes_lookup():
    assert parse_query("Is it open now?", has_selected_entity=True).intent == IntentType.PLACE_LOOKUP
