"""Regression tests for committed places planning logic.

Verifies that user-selected/committed places are never silently removed by normal recommendation filters,
budget limits, time window constraints, or missing/unknown opening hours.
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.models import Candidate, SourceRef
from app.planning.itinerary import PlanRequest, optimise


def _cand(cid: str, name: str, **kwargs) -> Candidate:
    base = {
        "id": cid,
        "name": name,
        "category": kwargs.pop("category", "park"),
        "kind": kwargs.pop("kind", "attraction"),
        "lat": kwargs.pop("lat", 12.9716),
        "lon": kwargs.pop("lon", 77.5946),
        "sources": [SourceRef("curated", "pack", confidence=0.9)],
        "confidence": 0.9,
    }
    base.update(kwargs)
    return Candidate(**base)


def test_regression_test1_four_selected_places_all_planned_when_hours_unknown():
    """Test 1: 4 selected places (Fantasy Park, Hoodi Lake Park, Vinayaka Layout Park, Garudacharapalya Lake Park)
    Make a Plan -> 4 selected, 4 planned, 0 excluded even when opening hours are unavailable.
    """
    places = [
        _cand("p1", "Fantasy Park", lat=12.98, lon=77.68, opening_hours=None),
        _cand("p2", "Hoodi Lake Park", lat=12.99, lon=77.71, opening_hours=None),
        _cand("p3", "Vinayaka Layout Park", lat=12.97, lon=77.69, opening_hours=None),
        _cand("p4", "Garudacharapalya Lake Park", lat=12.98, lon=77.70, opening_hours=None),
    ]
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    request = PlanRequest(
        candidates=places,
        start_point=(12.97, 77.68),
        start_label="Start",
        start_time=start,
        duration_min=180,  # 3 hours window
        locked_ids=["p1", "p2", "p3", "p4"],
    )

    plan = optimise(request)

    assert plan["selected_places"] == 4
    assert plan["planned_places"] == 4
    assert plan["excluded_places"] == 0
    assert len(plan["stops"]) == 4
    planned_ids = {s["poi_id"] for s in plan["stops"]}
    assert planned_ids == {"p1", "p2", "p3", "p4"}


def test_regression_test2_unknown_opening_hours_generates_warning_not_removal():
    """Test 2: Selected place has unknown opening hours.
    Expected: place included, warning generated, NOT removed.
    """
    places = [
        _cand("p1", "Fantasy Park", opening_hours=None),
    ]
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    request = PlanRequest(
        candidates=places,
        start_point=(12.97, 77.68),
        start_label="Start",
        start_time=start,
        duration_min=120,
        locked_ids=["p1"],
    )

    plan = optimise(request)

    assert len(plan["stops"]) == 1
    assert plan["stops"][0]["poi_id"] == "p1"
    assert plan["stops"][0]["opening_hours_status"] == "UNKNOWN"
    assert any("Opening hours for Fantasy Park are not verified" in w for w in plan["warnings"])


def test_regression_test3_selected_places_exceed_preferred_budget():
    """Test 3: Selected places exceed preferred budget.
    Expected: places retained, budget warning generated, within_budget = False.
    """
    places = [
        _cand("p1", "Luxury Resort Park", entry_cost="1500.00", fee_currency="INR"),
    ]
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    request = PlanRequest(
        candidates=places,
        start_point=(12.97, 77.68),
        start_label="Start",
        start_time=start,
        duration_min=120,
        budget_cap=Decimal("500.00"),
        locked_ids=["p1"],
    )

    plan = optimise(request)

    assert len(plan["stops"]) == 1
    assert plan["stops"][0]["poi_id"] == "p1"
    assert plan["totals"]["within_budget"] is False
    assert any("exceeds your target budget" in w for w in plan["warnings"])
    assert any(c["type"] == "budget_exceeded" for c in plan["constraint_conflicts"])


def test_regression_test4_selected_places_exceed_available_time_window():
    """Test 4: Selected places exceed available time.
    Expected: all selected places retained, constraint conflict displayed, end time extended.
    """
    places = [
        _cand("p1", "Park Alpha", visit_duration_min=120, lat=12.97, lon=77.68),
        _cand("p2", "Park Beta", visit_duration_min=120, lat=12.98, lon=77.69),
        _cand("p3", "Park Gamma", visit_duration_min=120, lat=12.99, lon=77.70),
    ]
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    request = PlanRequest(
        candidates=places,
        start_point=(12.97, 77.68),
        start_label="Start",
        start_time=start,
        duration_min=120,  # 2 hours preferred, but 3 places take ~6+ hours
        locked_ids=["p1", "p2", "p3"],
    )

    plan = optimise(request)

    assert plan["selected_places"] == 3
    assert plan["planned_places"] == 3
    assert len(plan["stops"]) == 3
    assert any("extending" in w and "beyond your preferred" in w for w in plan["warnings"])
    assert any(c["type"] == "time_window_exceeded" for c in plan["constraint_conflicts"])


def test_regression_test5_permanently_closed_place_flagged_not_silently_removed():
    """Test 5: Selected place is confirmed permanently closed.
    Expected: place flagged in unscheduled with status BLOCKED and reason Permanently closed, user informed.
    """
    places = [
        _cand("p1", "Open Park", permanently_closed=False),
        _cand("p2", "Defunct Park", permanently_closed=True),
    ]
    start = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    request = PlanRequest(
        candidates=places,
        start_point=(12.97, 77.68),
        start_label="Start",
        start_time=start,
        duration_min=180,
        locked_ids=["p1", "p2"],
    )

    plan = optimise(request)

    assert plan["selected_places"] == 2
    assert plan["planned_places"] == 1
    assert plan["excluded_places"] == 1
    assert any(u["id"] == "p2" and u["status"] == "BLOCKED" and u["reason"] == "Permanently closed" for u in plan["unscheduled"])
