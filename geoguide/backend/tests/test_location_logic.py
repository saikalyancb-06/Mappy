"""Location logic: which city a traveller is in, and how place names are resolved.

Regression for two field reports: a traveller in Bengaluru asking for places
"near city center" got results from Tallinn (a Tallinn district is literally
named "City Centre" in OpenStreetMap), and GPS points in large parts of
Bengaluru were not recognised as Bengaluru.
"""
import pytest
from sqlalchemy import delete

from app.db.models import Destination
from app.db.session import SessionLocal
from app.geo.city import resolve_city
from app.geo.geo_context import build_geo_context
from app.geo.geocoding import city_extent_km, resolve_place
from app.query.parser import parse_query
from app.services.query_service import AskRequest, QueryService

CENTRE = (12.971599, 77.594566)
WHITEFIELD = (12.9698, 77.7500)  # ~17 km from the centre, outside the dataset's 9.4 km spread
KORAMANGALA = (12.9352, 77.6245)
TALLINN_CITY_CENTRE = {"lat": "59.4370", "lon": "24.7536", "name": "City Centre", "addresstype": "suburb", "importance": 0.31, "display_name": "City Centre, Tallinn, Estonia", "address": {"city": "Tallinn", "country": "Estonia"}}


@pytest.fixture(scope="module", autouse=True)
def bengaluru():
    with SessionLocal() as db:
        db.merge(Destination(id="dest-test-blr", name="Bengaluru", region="Karnataka", country="India", country_code="IN", lat=CENTRE[0], lon=CENTRE[1], coverage_radius_km=9.4, timezone="Asia/Kolkata", population=13_200_000))
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(Destination).where(Destination.id == "dest-test-blr"))
        db.commit()


@pytest.fixture
def nominatim(monkeypatch):
    """A scripted Nominatim: `local` answers bounded (near-me) searches, `world` answers unbounded ones."""
    calls: list[dict] = []
    answers: dict[str, list] = {"local": [], "world": [], "reverse": None}

    def fake(provider, url, params=None, **kwargs):
        calls.append({"url": url, **(params or {})})
        if url.endswith("/reverse"):
            return answers["reverse"]
        return answers["local"] if params.get("bounded") else answers["world"]

    monkeypatch.setattr("app.geo.geocoding.get_json", fake)
    return answers, calls


def test_city_centre_means_the_centre_of_my_city_and_is_never_geocoded(nominatim, gps):
    answers, calls = nominatim
    answers["world"] = [TALLINN_CITY_CENTRE]
    intent = parse_query("find places near city center")
    assert intent.place_mention == "city center"
    geo = build_geo_context(user_location=gps(*KORAMANGALA), active_destination=None, place_mention=intent.place_mention, spatial_relation=intent.spatial_relation)
    assert geo.query_destination.kind == "centre" and geo.query_destination.name == "Bengaluru centre"
    assert (geo.reference.lat, geo.reference.lon) == CENTRE and geo.reference.semantic == "near_place"
    assert not [c for c in calls if c["url"].endswith("/search")]


def test_city_centre_follows_the_destination_being_explored(nominatim, gps):
    geo = build_geo_context(user_location=gps(*KORAMANGALA), active_destination={"id": "dest-testville"}, place_mention="the city centre", spatial_relation="near_place")
    assert geo.query_destination.name == "Testville centre" and (geo.reference.lat, geo.reference.lon) == (10.0, 20.0)


def test_city_centre_without_any_city_asks_instead_of_guessing(nominatim):
    geo = build_geo_context(user_location=None, active_destination=None, place_mention="downtown", spatial_relation="near_place")
    assert geo.reference is None and geo.reference_required == "location_or_destination"


def test_ask_near_city_centre_answers_for_my_city_and_never_switches_elsewhere(nominatim, gps):
    answers, _ = nominatim
    answers["world"] = [TALLINN_CITY_CENTRE]
    response = QueryService().ask(AskRequest(question="find places near city centre", user_location=gps(*KORAMANGALA)))
    reference = response["geo_context"]["reference"]
    assert reference["label"] == "Bengaluru centre" and reference["destination_id"] == "dest-test-blr"
    adopted = response["next_active_destination"]
    assert adopted is None or adopted["destination_id"] == "dest-test-blr"  # at most your own city, never elsewhere
    assert "Tallinn" not in str(response["results"])


def test_a_far_away_district_with_the_same_name_is_rejected(nominatim):
    answers, _ = nominatim
    answers["world"] = [{**TALLINN_CITY_CENTRE, "name": "Kesklinn"}]
    place, _ = resolve_place("Kesklinn", near=CENTRE)
    assert place is None


def test_a_named_city_elsewhere_is_still_found(nominatim):
    answers, _ = nominatim
    answers["world"] = [{"lat": "59.4372", "lon": "24.7454", "name": "Tallinn", "addresstype": "city", "importance": 0.7, "address": {"city": "Tallinn", "country": "Estonia"}}]
    place, _ = resolve_place("Tallinn", near=CENTRE)
    assert place.kind == "geocoded" and place.name == "Tallinn" and place.country == "Estonia"


def test_names_are_looked_up_near_the_traveller_first(nominatim):
    answers, calls = nominatim
    answers["local"] = [{"lat": "12.9784", "lon": "77.6408", "name": "Indiranagar", "addresstype": "suburb", "importance": 0.3, "address": {"city": "Bengaluru", "country": "India"}}]
    answers["world"] = [TALLINN_CITY_CENTRE]
    place, _ = resolve_place("Indiranagar", near=CENTRE)
    assert place.name == "Indiranagar" and place.kind == "poi" and place.destination_id == "dest-test-blr"
    first = calls[0]
    assert first["bounded"] == 1 and first["accept-language"] == "en" and "viewbox" in first


def test_generic_names_are_never_searched_worldwide(nominatim):
    _, calls = nominatim
    place, _ = resolve_place("bus stand", near=CENTRE)
    assert place is None and all(c.get("bounded") == 1 for c in calls if c["url"].endswith("/search"))


def test_whole_metro_area_is_recognised_as_the_city():
    assert city_extent_km(Destination(coverage_radius_km=9.4, population=13_200_000)) > 20
    assert city_extent_km(Destination(coverage_radius_km=10.0, population=3000)) == 10.0  # small towns keep their coverage
    city, errors = resolve_city(lat=WHITEFIELD[0], lon=WHITEFIELD[1])
    assert city.name == "Bengaluru" and city.resolved_by == "coverage" and not errors


def test_old_city_names_resolve_to_the_stored_city():
    place, _ = resolve_place("Bangalore", allow_remote=False)
    assert place.kind == "destination" and place.destination_id == "dest-test-blr"


def test_reverse_geocoded_alias_matches_the_stored_city(nominatim):
    answers, calls = nominatim
    answers["reverse"] = {"address": {"suburb": "Hoskote", "city": "Bangalore", "state": "Karnataka", "country": "India"}}
    city, _ = resolve_city(lat=CENTRE[0] + 0.23, lon=CENTRE[1])  # ~25 km north: outside the extent
    assert city.destination_id == "dest-test-blr" and city.resolved_by == "geocoder_match"
    assert calls[0]["accept-language"] == "en"
