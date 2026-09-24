"""Spatial candidate generation, hard filters and ranking."""
from datetime import datetime, timezone

from app.geo.distance import haversine_km
from app.geo.spatial import SpatialQuery, nearby
from app.models import Candidate, SourceRef
from app.ranking.ranker import RankRequest, rank


def test_radius_is_a_hard_constraint_and_distances_are_exact():
    results = nearby(SpatialQuery(lat=10.0, lon=20.0, radius_km=1.0))
    assert results, "expected nearby POIs"
    for candidate in results:
        assert candidate.distance_km <= 1.0
        assert abs(candidate.distance_km - haversine_km(10.0, 20.0, candidate.lat, candidate.lon)) < 1e-3
    assert [c.distance_km for c in results] == sorted(c.distance_km for c in results)
    assert "tv-far-lake" not in {c.id for c in results}


def test_category_filter_is_hard():
    results = nearby(SpatialQuery(lat=10.0, lon=20.0, radius_km=10.0, categories=["cafe"]))
    assert {c.category for c in results} == {"cafe"}


def test_invalid_rows_never_load():
    assert "tv-bad-coords" not in {c.id for c in nearby(SpatialQuery(lat=10.0, lon=20.0, radius_km=500))}


def _candidate(cid, lat, lon, **kwargs):
    base = dict(id=cid, name=cid, category=kwargs.pop("category", "monument"), kind="attraction", lat=lat, lon=lon, sources=[SourceRef("curated", "test")], confidence=0.9)
    base.update(kwargs)
    return Candidate(**base)


def test_step_free_requirement_excludes_known_inaccessible_places():
    candidates = [_candidate("steps", 10.0, 20.0, step_free=False), _candidate("ramp", 10.0, 20.001, step_free=True), _candidate("unknown", 10.0, 20.002)]
    result = rank(candidates, RankRequest(reference=(10.0, 20.0), radius_km=5, user={"accessibility": ["step_free"]}))
    ids = [c.id for c in result.ranked]
    assert "steps" not in ids and ids[0] == "ramp"
    assert any(r["reason"] == "not_step_free" for r in result.filtered_out)


def test_open_now_filter_and_boost():
    morning = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    open_place = _candidate("open", 10.0, 20.0, opening_hours={"weekly": {"daily": [["08:00", "18:00"]]}})
    closed_place = _candidate("closed", 10.0, 20.0, opening_hours={"weekly": {"daily": [["16:00", "20:00"]]}})
    unknown = _candidate("unknown", 10.0, 20.0)
    result = rank([closed_place, unknown, open_place], RankRequest(reference=(10.0, 20.0), radius_km=5, local_time=morning, time_sensitive=True, require_open=True))
    assert [c.id for c in result.ranked] == ["open", "unknown"]
    assert result.ranked[0].open_status == "open" and "Open now until 18:00" in result.ranked[0].reasons


def test_interest_preferences_reorder_without_breaking_radius():
    temple = _candidate("temple", 10.0, 20.01, category="temple")
    museum = _candidate("museum", 10.0, 20.01, category="museum")
    far = _candidate("far-temple", 11.0, 20.0, category="temple")
    heritage_lover = rank([museum, temple, far], RankRequest(reference=(10.0, 20.0), radius_km=5, user={"interests": {"culture": 1}}))
    assert "far-temple" not in [c.id for c in heritage_lover.ranked]
    assert heritage_lover.ranked[0].id == "museum"
    assert any("Culture" in reason for reason in heritage_lover.ranked[0].reasons)


def test_weather_prefers_sheltered_places_in_heat():
    exposed = _candidate("exposed", 10.0, 20.0, indoor=False, walking_effort="high")
    sheltered = _candidate("sheltered", 10.0, 20.0, indoor=True)
    result = rank([exposed, sheltered], RankRequest(reference=(10.0, 20.0), radius_km=5, weather_signals=["heat"]))
    assert result.ranked[0].id == "sheltered"


def test_every_ranked_candidate_explains_its_score():
    result = rank([_candidate("a", 10.0, 20.0, rating=4.6, review_count=900)], RankRequest(reference=(10.0, 20.0), reference_label="Old Fort", radius_km=5))
    ranked = result.ranked[0]
    assert set(ranked.scores) == {"relevance", "geographic", "quality", "open", "preference", "weather", "source_confidence", "vibe", "community"}
    assert any("Rated 4.6" in reason for reason in ranked.reasons)


def test_precision_at_3_for_labelled_profiles():
    """Small labelled set: for each profile the relevant places should fill the top 3."""
    pool = [
        _candidate("t1", 10.0, 20.001, category="temple", tags=["peaceful"]),
        _candidate("t2", 10.0, 20.002, category="temple", tags=["peaceful"]),
        _candidate("m1", 10.0, 20.001, category="museum", indoor=True, tags=["family"]),
        _candidate("v1", 10.0, 20.003, category="viewpoint", tags=["sunset", "peaceful"]),
        _candidate("c1", 10.0, 20.001, category="cafe", kind="food"),
        _candidate("r1", 10.0, 20.002, category="restaurant", kind="food"),
    ]
    cases = [
        (RankRequest(reference=(10.0, 20.0), radius_km=5, preferences=["peaceful"]), {"t1", "t2", "v1"}),
        (RankRequest(reference=(10.0, 20.0), radius_km=5, requested_group="food", categories={"cafe", "restaurant", "bakery"}), {"c1", "r1"}),
    ]
    precisions = []
    for request, relevant in cases:
        top = [c.id for c in rank([Candidate(**{**c.__dict__, "scores": {}, "reasons": []}) for c in pool], request).ranked[:3]]
        precisions.append(len(set(top) & relevant) / min(3, len(relevant)))
    assert sum(precisions) / len(precisions) >= 0.75
