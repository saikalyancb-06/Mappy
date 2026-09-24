"""GeoContext rules: physical location vs destination, freshness, no fabricated locations."""
from app.geo.geo_context import build_geo_context, parse_user_location

TESTVILLE = {"id": "dest-testville"}


def test_gps_available_is_fresh_and_placed_in_destination(gps):
    location, status, warnings = parse_user_location(gps(10.001, 20.001))
    assert status == "available" and not warnings
    assert location.inside_destination_id == "dest-testville"


def test_gps_outside_any_destination_has_no_invented_area(gps):
    location, status, _ = parse_user_location(gps(-33.0, 151.0))
    assert status == "available"
    assert location.inside_destination_id is None and location.area_name is None


def test_missing_gps_is_unavailable_not_a_default_city():
    context = build_geo_context(user_location=None, active_destination=None)
    assert context.location_status == "unavailable"
    assert context.reference is None
    assert context.reference_required == "location_or_destination"


def test_stale_gps_is_flagged_and_expired_gps_is_rejected(gps):
    _, status, warnings = parse_user_location(gps(10.0, 20.0, age_s=20 * 60))
    assert status == "stale" and "minutes ago" in warnings[0]
    location, status, _ = parse_user_location(gps(10.0, 20.0, age_s=3 * 3600))
    assert location is None and status == "unavailable"


def test_low_accuracy_and_invalid_payloads(gps):
    location, _, warnings = parse_user_location(gps(10.0, 20.0, accuracy=2500))
    assert location.accuracy_level == "low" and any("approximate" in w for w in warnings)
    assert parse_user_location({"lat": 95, "lon": 20, "timestamp": "2026-01-01T00:00:00Z"})[0] is None
    assert parse_user_location({"lat": 10, "lon": 20})[0] is None  # no timestamp → freshness unknown
    assert parse_user_location({"lat": 0, "lon": 0, "timestamp": "2026-01-01T00:00:00Z"})[0] is None  # null island


def test_near_me_uses_gps_even_with_an_active_destination(gps):
    context = build_geo_context(user_location=gps(-33.0, 151.0), active_destination=TESTVILLE, spatial_relation="near_me")
    assert context.reference.origin == "user_location"
    assert (context.reference.lat, context.reference.lon) == (-33.0, 151.0)


def test_near_me_without_gps_asks_for_location_instead_of_guessing():
    context = build_geo_context(user_location=None, active_destination=TESTVILLE, spatial_relation="near_me")
    assert context.reference is None and context.reference_required == "user_location"


def test_explicit_destination_wins_over_gps(gps):
    context = build_geo_context(user_location=gps(-33.0, 151.0), active_destination=None, place_mention="Testville", spatial_relation="in_place")
    assert context.reference.origin == "query_destination"
    assert context.reference.destination_id == "dest-testville"
    assert context.reference.semantic == "in_destination"


def test_destination_context_persists_when_question_names_no_place(gps):
    context = build_geo_context(user_location=gps(-33.0, 151.0), active_destination=TESTVILLE)
    assert context.reference.origin == "active_destination"


def test_poi_anchor_uses_local_radius():
    context = build_geo_context(user_location=None, active_destination=None, place_mention="Old Fort", spatial_relation="near_place")
    assert context.reference.semantic == "near_place" and context.reference.radius_km == 2.0


def test_unresolvable_place_is_reported_not_substituted():
    context = build_geo_context(user_location=None, active_destination=TESTVILLE, place_mention="Nowhereville Xyz", spatial_relation="in_place")
    assert context.reference is None and context.reference_required == "resolvable_place"
    assert any("Nowhereville" in warning for warning in context.warnings)
