"""Plan wishes: per-item quantities, balanced coverage, honest gaps, and the swipe deck."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.models import Poi
from app.db.session import SessionLocal
from app.geo.geo_context import build_geo_context
from app.main import app
from app.query.constraints import parse_constraints
from app.services.plan_service import build_plan

EXTRA = [
    # Two parks, so "a park" must mean exactly one of them.
    Poi(id="wx-park-a", destination_id="dest-testville", name="Riverside Park", kind="attraction", category="park", tags='["peaceful"]', lat=10.004, lon=20.002, opening_hours='{"always_open": true}', entry_cost="0.00", entry_fee=0.0, fee_currency="INR", visit_duration_min=40),
    Poi(id="wx-park-b", destination_id="dest-testville", name="Mango Grove Park", kind="attraction", category="park", tags='["shade"]', lat=10.006, lon=20.001, opening_hours='{"always_open": true}', entry_cost="0.00", entry_fee=0.0, fee_currency="INR", visit_duration_min=40),
    Poi(id="wx-temple-b", destination_id="dest-testville", name="Hill Shrine", kind="attraction", category="temple", tags='["spiritual"]', lat=10.003, lon=20.004, opening_hours='{"always_open": true}', entry_cost="0.00", entry_fee=0.0, fee_currency="INR", visit_duration_min=20),
    # Tagged "sunset" but a bar is not a sunset spot.
    Poi(id="wx-sunset-bar", destination_id="dest-testville", name="Sundowner Bar", kind="food", category="bar", tags='["sunset"]', lat=10.002, lon=20.002, opening_hours='{"always_open": true}', entry_cost="0.00", entry_fee=0.0, fee_currency="INR", visit_duration_min=60),
]


@pytest.fixture(scope="module", autouse=True)
def extra_places():
    from app.core.text import normalize

    with SessionLocal() as db:
        for poi in EXTRA:
            poi.normalized_name = normalize(poi.name)
            db.merge(poi)
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(Poi).where(Poi.id.in_([p.id for p in EXTRA])))
        db.commit()


# ---- reading the wishes -----------------------------------------------------------------------

def test_the_reported_request_is_read_correctly():
    c = parse_constraints("temples and the sunset spot no museums and 500 and and a park")
    assert c.exclude_categories == ["museum"]  # "no" does not spill over to "… and a park"
    assert c.max_cost == "500.00" and c.cost_currency is None  # a bare amount is a budget in the local currency
    assert [(w["key"], w["quantity"]) for w in c.wishes] == [("temple", None), ("spot:sunset", 1), ("park", 1)]
    assert "Looking for: temples · 1 sunset spot · 1 park" in c.understood


@pytest.mark.parametrize("text, excluded", [
    ("no museums or parks", {"museum", "park"}),
    ("no museums, and a park", {"museum"}),
    ("avoid museums but a park is fine", {"museum"}),
])
def test_negation_scope(text, excluded):
    assert set(parse_constraints(text).exclude_categories) == excluded


@pytest.mark.parametrize("text, cost", [
    ("temples under 300", "300.00"), ("temples, 500 and a park", "500.00"), ("budget 1200", "1200.00"),
    ("within 500 m", None), ("I have 2 hours", None), ("temples within 20 min", None), ("free from 4-8 PM", None),
])
def test_amounts_without_currency(text, cost):
    assert parse_constraints(text).max_cost == cost


@pytest.mark.parametrize("text, quantities", [
    ("two temples and a lake", {"temple": 2, "lake": 1}),
    ("some temples", {"temple": None}),
    ("the temples and one park", {"temple": None, "park": 1}),
])
def test_quantities(text, quantities):
    assert {w["key"]: w["quantity"] for w in parse_constraints(text).wishes} == quantities


# ---- plans cover every wish, cap singular ones, explain gaps ------------------------------------

def _plan(wishes, weather_ok, **kwargs):
    weather_ok()
    geo = build_geo_context(user_location=None, active_destination={"id": "dest-testville"})
    plan, _, _ = build_plan(geo=geo, profile={}, wishes=wishes, day_offset=1, start=kwargs.pop("start", "09:00"), duration_min=kwargs.pop("duration_min", 600), **kwargs)
    return plan


def test_plan_covers_each_wish_and_caps_a_park(weather_ok):
    plan = _plan("temples and a sunset spot, no museums and a park", weather_ok)
    categories = [s["category"] for s in plan["stops"]]
    assert categories.count("park") == 1  # "a park" → exactly one
    assert "temple" in categories and "tv-sunset-rock" in [s["poi_id"] for s in plan["stops"]]
    assert "museum" not in categories and "bar" not in categories  # a sunset-tagged bar is not a sunset spot
    assert {c["wish"]: c["status"] for c in plan["wish_coverage"]} == {"temples": "planned", "1 sunset spot": "planned", "1 park": "planned"}


def test_sunset_spot_is_timed_for_sunset(weather_ok):
    plan = _plan("temples and a sunset spot", weather_ok, start="09:00", duration_min=720)
    rock = next(s for s in plan["stops"] if s["poi_id"] == "tv-sunset-rock")
    assert rock["arrive"] == "17:35" and rock["timing_note"] == "timed for sunset (18:20)"  # fake forecast sunset 18:20, 45 min lead
    assert rock is plan["stops"][-1]


def test_missing_and_unaffordable_wishes_are_explained(weather_ok):
    plan = _plan("a beach and a museum, under ₹10", weather_ok)
    coverage = {c["wish"]: c for c in plan["wish_coverage"]}
    assert coverage["1 beach"]["status"] == "none_available" and "No verified beaches found in Testville" in coverage["1 beach"]["note"]
    assert coverage["1 museum"]["status"] == "over_budget" and "Testville Museum" in coverage["1 museum"]["note"] and "₹20" in coverage["1 museum"]["note"]
    assert coverage["1 beach"]["note"] in plan["warnings"]


def test_liked_places_are_not_forced_into_swipe_order(weather_ok):
    # Lotus Temple closes 12:00–16:00; liking it before Old Fort must not force it first.
    plan = _plan(None, weather_ok, start="12:30", duration_min=360, locked_ids=["tv-lotus-temple", "tv-old-fort"])
    ids = [s["poi_id"] for s in plan["stops"]]
    assert {"tv-lotus-temple", "tv-old-fort"} <= set(ids) and ids.index("tv-old-fort") < ids.index("tv-lotus-temple")


# ---- swipe deck ---------------------------------------------------------------------------------

def test_deck_takes_turns_between_wishes_and_respects_swipes(weather_ok):
    weather_ok()
    with TestClient(app) as client:
        body = {"active_destination": {"id": "dest-testville"}, "wishes": "temples and a sunset spot and a park"}
        deck = client.post("/api/plan/deck", json=body).json()
        wishes = [card["scores"]["wish"] for card in deck["cards"]]
        assert wishes[:3] == ["temples", "1 sunset spot", "1 park"]  # interleaved, not all of one kind
        assert all(card["category"] in {"temple", "viewpoint", "park"} for card in deck["cards"])
        skipped = deck["cards"][0]["id"]
        again = client.post("/api/plan/deck", json={**body, "excluded_ids": [skipped]}).json()
        assert skipped not in [card["id"] for card in again["cards"]]
        plan = client.post("/api/plan", json={**body, "duration": "full", "day_offset": 1, "start": "09:00", "locked_ids": ["wx-park-b"], "excluded_ids": [skipped]}).json()["plan"]
        ids = [s["poi_id"] for s in plan["stops"]]
        assert "wx-park-b" in ids and "wx-park-a" not in ids and skipped not in ids  # the liked park is the park


def test_nearby_interleaves_wishes():
    with TestClient(app) as client:
        items = client.get("/api/nearby", params={"destination_id": "dest-testville", "text": "temples and a park"}).json()["items"]
        assert [i["category"] for i in items[:2]] in (["temple", "park"], ["park", "temple"])
