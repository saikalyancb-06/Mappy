"""Organiser-dataset import, NL constraints, cost/budget, hotels, search, plans, routes, confidence."""
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.import_ps13 import import_ps13
from app.geo.geo_context import build_geo_context
from app.main import app
from app.models import Candidate, SourceRef
from app.query.constraints import parse_constraints
from app.ranking.ranker import RankRequest, rank
from app.search.aggregator import aggregate
from app.search.serpapi import SearchResponse, SearchResult, SerpApiClient
from app.services.cost import cost_for_user, sum_money
from app.services.hotels import HotelRequest, find_hotels
from app.services.plan_service import ReplanState, build_plan
from app.services.route_suggestions import RouteRequest, route_suggestions
from app.weather.open_meteo import get_weather

SCHEMA = Path(__file__).resolve().parent.parent / "data" / "sources" / "ps13" / "schema.sqlite.sql"
TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """A tiny database in the organiser's exact schema: one city matching Testville, one new city."""
    path = tmp_path_factory.mktemp("ps13") / "mini.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.read_text().replace("PRAGMA foreign_keys = ON;", ""))
    ts = "2026-08-18T10:00:00+05:30"
    db.execute("INSERT INTO categories VALUES ('cat_m', 'museum_history', 'History museums', NULL, 'poi', ?)", (ts,))
    db.execute("INSERT INTO categories VALUES ('cat_r', 'religious', 'Religious', NULL, 'poi', ?)", (ts,))
    db.execute("INSERT INTO countries VALUES ('cnt_t', 'TL', 'TLD', 'Testland', 'INR', '+0', 'Test', ?)", (ts,))
    for cid, name, lat, lon in (("cty_tv", "Testville", 10.001, 20.001), ("cty_nw", "Newtown", 30.0, 40.0)):
        db.execute("INSERT INTO cities VALUES (?, ?, 'State', 'cnt_t', 'TL', ?, ?, 'UTC', 'Test', 1000, 'winter', '11,12', 'en-IN', 'desc', 'active', ?)", (cid, name, lat, lon, ts))
    pois = [
        ("poi_a", "cty_tv", "Clock Museum", "cat_m", "museum", 10.004, 20.004, 60, "350.00", "10:00", "17:00", "0", "step_free", "indoor,quiet"),
        ("poi_b", "cty_tv", "Old Fort", "cat_r", "religious", 10.05, 20.05, 45, "100.00", "06:00", "19:00", None, "partial", "crowded,unesco"),
        ("poi_c", "cty_nw", "Harbour Shrine", "cat_r", "religious", 30.001, 40.001, 30, "0.00", None, "18:00", None, "none", "quiet"),
    ]
    for p in pois:
        db.execute("INSERT INTO activities_poi VALUES (?,?,?,?,?,?,?,?,?, 'INR', 1.5, 40, 70, ?, ?, ?, 'winter', ?, ?, 'd', 0, 'active', ?)", (*p[:9], *p[9:], ts))
    db.execute("INSERT INTO hotels VALUES ('htl_x', 'cty_tv', 'Lake View Lodge', 'guesthouse', 3, 8.6, 120, '1 Lake Rd', 10.002, 20.003, 0.4, 'd', 'INR', '14:00', '11:00', NULL, 0, 'active', ?, ?)", (ts, ts))
    db.execute("INSERT INTO place_kb VALUES ('kbc_1', 'cty_tv', NULL, 'history', 'Testville — History', 'The dataset says the clock tower dates from 1901.', 'en-IN', 20, 'KV Place Guide', NULL, NULL, ?)", (ts,))
    db.execute("INSERT INTO poi_facts_kb VALUES ('fct_1', 'poi_a', 'trivia', 'The museum holds 300 clocks.', 'en-IN', 'high', 1, NULL, ?)", (ts,))
    now = datetime.now(timezone.utc)
    db.execute("INSERT INTO safety_advisories VALUES ('adv_1', 'cty_tv', 'transport', 'warning', 'Road works', 'Expect delays.', 'en-IN', ?, ?, 'Municipality', NULL, NULL, 'active', ?)", ((now - timedelta(days=1)).isoformat(), (now + timedelta(days=5)).isoformat(), ts))
    db.execute("INSERT INTO events_festivals VALUES ('evt_1', 'cty_tv', 'Clock Fair', 'cat_m', ?, ?, 'winter', 'annual', 100, 1, '50.00', 'INR', NULL, NULL, 'Fair.', 'active', ?)", (TODAY.isoformat(), (TODAY + timedelta(days=2)).isoformat(), ts))
    db.execute("INSERT INTO weather_daily VALUES ('wth_1', 'cty_tv', ?, 22.0, 36.5, 39.0, 0, 40, 10, 'clear', 'winter', 0, ?)", (TODAY.isoformat(), ts))
    db.commit()
    db.close()
    counts = import_ps13(path)
    return counts


def test_dataset_import_is_additive_and_attaches_to_existing_destination(dataset):
    from app.db.models import Destination, EventFestival, Poi, SafetyAdvisory
    from app.db.session import SessionLocal

    assert dataset["pois"] == 3 and dataset["hotels"] == 1 and dataset["destinations"] == 1
    with SessionLocal() as db:
        museum = db.get(Poi, "poi_a")
        assert museum.destination_id == "dest-testville"  # same place as the curated pack
        assert museum.entry_cost == "350.00" and museum.fee_currency == "INR"  # exact decimal text
        assert '"closed": ["mon"]' in museum.opening_hours  # 0 = Monday
        assert db.get(Poi, "poi_c").opening_hours is None  # a missing opening time means unknown hours
        hotel = db.get(Poi, "htl_x")
        assert hotel.kind == "stay" and hotel.guest_score == 8.6
        assert db.get(Destination, "cty_nw").curated is False
        assert db.get(SafetyAdvisory, "adv_1").severity == "warning"  # severity kept as issued
        assert db.get(EventFestival, "evt_1").ticket_price == "50.00"


def test_dataset_weather_is_a_labelled_fallback(dataset):
    weather = get_weather(10.0, 20.0, 0)  # live provider is offline in tests
    assert weather["status"] == "ok" and weather["live"] is False
    assert weather["current"] is None and "heat" in weather["signals"]
    assert get_weather(-40.0, -40.0, 0)["status"] == "unavailable"  # no dataset there: nothing invented


def test_same_name_in_same_destination_merges_with_trusted_source_first():
    curated = Candidate(id="c1", name="Old Fort", category="monument", kind="attraction", lat=10.0, lon=20.0, destination_id="d", sources=[SourceRef("curated", "pack", confidence=0.95)])
    dataset_row = Candidate(id="c2", name="Old Fort", category="temple", kind="attraction", lat=10.05, lon=20.05, destination_id="d", sources=[SourceRef("dataset", "ds", confidence=0.6)])
    merged, duplicates = aggregate([[dataset_row], [curated]])
    assert duplicates == 1 and merged[0].id == "c1" and merged[0].lat == 10.0
    assert any(c["field"] == "location" for c in merged[0].conflicts)  # 7 km apart: recorded, not hidden


@pytest.mark.parametrize("text,expect", [
    ("I have 2 hours, ₹800, I'm alone, it's raining, and I want somewhere peaceful", {"available_minutes": 120, "max_cost": "800.00", "party": "solo", "raining": True, "crowd": "low"}),
    ("I'm free from 4–8 PM", {"window_start": "16:00", "window_end": "20:00", "available_minutes": 240}),
    ("within 5 km, open now, costs less than ₹₹, isn't crowded, good reviews, by bike in 20 minutes", {"open_now": True, "max_price_level": 2, "min_rating": 4.0, "travel_mode": "motorbike", "max_travel_min": 20, "crowd": "low"}),
    ("Give me 3 places only", {"limit": 3}),
    ("temples, no museums, under 500 rupees", {"include_categories": ["temple"], "exclude_categories": ["museum"], "max_cost": "500.00"}),
    ("hidden gems please", {"ranking_mode": "hidden_gems"}),
    ("going from Old Fort to Clock Museum, anything on the way", {"route_origin": "Old Fort", "route_destination": "Clock Museum"}),
])
def test_constraint_engine(text, expect):
    c = parse_constraints(text).as_dict()
    for key, value in expect.items():
        assert c[key] == value, (key, c[key])


def _cand(cid, **kw):
    base = dict(id=cid, name=cid, category=kw.pop("category", "monument"), kind=kw.pop("kind", "attraction"), lat=kw.pop("lat", 10.0), lon=kw.pop("lon", 20.0), sources=[SourceRef("curated", "t", confidence=0.9)], confidence=0.9)
    base.update(kw)
    return Candidate(**base)


def test_constraints_are_hard_filters_in_ranking():
    c = parse_constraints("good reviews, not crowded, by bike in 10 minutes, under ₹200")
    pool = [
        _cand("ok", rating=4.5, entry_cost="100.00", fee_currency="INR", lat=10.01),
        _cand("low_rating", rating=3.2),
        _cand("crowded", rating=4.6, tags=["crowded"]),
        _cand("far", rating=4.8, lat=10.2),
        _cand("pricey", rating=4.7, entry_cost="350.00", fee_currency="INR"),
    ]
    result = rank(pool, RankRequest(reference=(10.0, 20.0), radius_km=50, constraints=c))
    assert [x.id for x in result.ranked] == ["ok"]
    reasons = {f["id"]: f["reason"] for f in result.filtered_out}
    assert reasons == {"low_rating": "rating_below_minimum", "crowded": "crowded", "far": "too_far_to_travel", "pricey": "above_max_cost"}
    assert result.ranked[0].travel_min is not None and result.ranked[0].bars["cost"] > 0


def test_hidden_gems_are_defined_by_data_not_labels():
    c = parse_constraints("hidden gems")
    pool = [_cand("famous", popularity_score=95), _cand("quiet_one", popularity_score=30, tags=["peaceful"])]
    assert [x.id for x in rank(pool, RankRequest(reference=(10.0, 20.0), radius_km=5, constraints=c)).ranked] == ["quiet_one"]


def test_cost_and_budget_are_exact_and_currency_aware():
    profile = {"max_daily_budget": "1000.00", "budget_currency": "INR"}
    assert cost_for_user(_cand("a", entry_cost="350.00", fee_currency="INR"), profile)["fits_budget"] is True
    over = cost_for_user(_cand("b", entry_cost="1500.00", fee_currency="INR"), profile)
    assert over["fits_budget"] is False and over["share_of_budget_pct"] == 150
    assert cost_for_user(_cand("c", entry_cost="20.00", fee_currency="USD"), profile)["fits_budget"] is None
    assert cost_for_user(_cand("d", kind="stay"), profile)["kind"] == "unknown"  # no invented hotel price
    total, currency, complete = sum_money([("0.10", "INR"), ("0.20", "INR"), (None, "INR")])
    assert total == Decimal("0.30") and currency == "INR" and complete is False


class FakeHotels(SerpApiClient):
    def __init__(self):
        super().__init__(api_key="k")

    def search(self, query, **kwargs):
        results = [
            SearchResult("Lake View Lodge", None, "", "Google Hotels", "google_hotels", latitude=10.0021, longitude=20.0031, rating=4.4, review_count=300, price_per_night="2400.00", price_currency="INR", hotel_class=3, place_id="h1"),
            SearchResult("Budget Inn", None, "", "Google Hotels", "google_hotels", latitude=10.003, longitude=20.001, rating=3.9, price_per_night="900.00", price_currency="INR", hotel_class=2, place_id="h2"),
        ]
        return SearchResponse(query, "google_hotels", results, datetime.now(timezone.utc).isoformat())


def test_hotels_merge_live_rates_and_sort(dataset):
    geo = build_geo_context(user_location=None, active_destination={"id": "dest-testville"})
    result = find_hotels(HotelRequest(reference=geo.reference, profile={"max_daily_budget": "2000.00", "budget_currency": "INR"}, sort="cheapest"), web=FakeHotels())
    names = [h.name for h in result["items"]]
    assert names[0] == "Budget Inn"  # cheapest known rate first
    lodge = next(h for h in result["items"] if h.name == "Lake View Lodge")
    assert lodge.price_per_night == "2400.00" and lodge.guest_score == 8.6  # dataset + live merged
    assert lodge.cost_for_user["fits_budget"] is False
    grand = next(h for h in result["items"] if h.name == "Grand Hotel Testville")
    assert grand.price_per_night is None  # stored hotel without a live rate keeps no price


def test_plan_wishes_include_exclude_budget_and_must_see(weather_ok):
    weather_ok()
    geo = build_geo_context(user_location=None, active_destination={"id": "dest-testville"})
    plan, _, _ = build_plan(geo=geo, profile={}, wishes="temples and viewpoints, no museums, under ₹300, must see Sunset Rock", day_offset=1, start="09:00", duration_key="full")
    ids = [s["poi_id"] for s in plan["stops"]]
    assert "tv-sunset-rock" in ids and "tv-museum" not in ids
    assert all(s["category"] in {"temple", "viewpoint"} or s["locked"] for s in plan["stops"])
    assert Decimal(plan["totals"]["cost_exact"]) <= Decimal("300.00") and plan["totals"]["within_budget"] is True
    assert any("Must include" in line for line in plan["understood"])


def test_replan_from_current_state_moves_forward(weather_ok):
    weather_ok()
    geo = build_geo_context(user_location=None, active_destination={"id": "dest-testville"})
    plan, _, _ = build_plan(geo=geo, profile={}, day_offset=1, start="09:00", duration_key="full")
    first = plan["stops"][0]
    again, _, _ = build_plan(geo=geo, profile={}, day_offset=1, replan=ReplanState(previous=plan, current_stop_id=first["poi_id"], extra_minutes=30, now=first["arrive"]))
    assert first["poi_id"] not in [s["poi_id"] for s in again["stops"]]
    planned_depart = datetime.strptime(first["depart"], "%H:%M")
    assert again["stops"] and datetime.strptime(again["stops"][0]["arrive"], "%H:%M") >= planned_depart + timedelta(minutes=30)
    assert again["replanned"] and again["window_min"] <= plan["window_min"]


def test_route_suggestions_respect_detour_limit():
    result = route_suggestions(RouteRequest(origin=(9.99, 19.99), origin_label="A", destination=(10.03, 20.03), destination_label="B", travel_mode="walk", max_detour_min=20))
    assert result["items"]
    assert all(c.detour_min <= 20 for c in result["items"])
    assert "tv-far-lake" not in [c.id for c in result["items"]]


def test_api_search_hotels_compare_and_nl_nearby(dataset):
    with TestClient(app) as client:
        found = client.get("/api/search", params={"q": "sunset rok", "destination_id": "dest-testville"}).json()
        assert found["places"][0]["id"] == "tv-sunset-rock"  # typo-tolerant
        assert client.get("/api/search", params={"q": "Moon Palace", "destination_id": "dest-testville"}).json()["places"] == []
        hotels = client.get("/api/hotels", params={"destination_id": "dest-testville"}).json()
        assert {h["name"] for h in hotels["items"]} >= {"Lake View Lodge", "Grand Hotel Testville"}
        compare = client.post("/api/ask", json={"question": "Compare Old Fort and Lotus Temple", "active_destination": {"id": "dest-testville"}}).json()
        assert compare["intent"]["intent"] == "COMPARE" and len(compare["comparison"]["columns"]) == 2
        nearby = client.get("/api/nearby", params={"destination_id": "dest-testville", "text": "quiet, no museums, free"}).json()
        assert nearby["understood"] and all(i["category"] != "museum" for i in nearby["items"])
        decide = client.post("/api/ask", json={"question": "Give me 2 places only", "active_destination": {"id": "dest-testville"}}).json()
        assert len(decide["results"]) <= 2


def test_route_endpoint_names_are_not_categories_and_endpoints_are_not_suggested():
    from app.query.parser import parse_query

    intent = parse_query("Something on my way from Old Fort to Lotus Temple")
    assert intent.intent.value == "ROUTE_SUGGESTIONS" and intent.category is None
    assert intent.constraints.include_categories == []
    cafes = parse_query("any cafes on my way from Old Fort to Lotus Temple")
    assert cafes.category == "cafe"
    result = route_suggestions(RouteRequest(origin=(10.0, 20.0), origin_label="Old Fort", destination=(10.03, 20.03), destination_label="Sunset Rock", user={"travel_mode": "bicycle"}, max_detour_min=30))
    ids = [c.id for c in result["items"]]
    assert "tv-old-fort" not in ids and "tv-sunset-rock" not in ids
    assert result["mode"] == "bicycle"  # the traveller's own transport


def test_live_hotel_rate_without_stated_currency_uses_requested_currency():
    payload = {"properties": [{"name": "Plain Inn", "gps_coordinates": {"latitude": 10.0, "longitude": 20.0}, "rate_per_night": {"extracted_lowest": 999}}]}
    parsed = SerpApiClient.parse(payload, "google_hotels")
    assert parsed[0].price_per_night == "999.00" and parsed[0].price_currency is None
    stay = Candidate(id="s", name="Plain Inn", kind="stay", category="hotel", lat=10.0, lon=20.0, price_per_night="999.00")
    assert cost_for_user(stay, {"max_daily_budget": "2000", "budget_currency": "INR"})["fits_budget"] is None
