"""City intelligence: registry, enrichment, SerpApi Maps normalisation, dedupe, boundaries, freshness,
routing (database-first), review-derived vibes and the recommendation policy."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.cities import enrichment as enrichment_module
from app.cities import service as city_service
from app.cities.enrichment import run_enrichment
from app.cities.knowledge import collect as collect_knowledge
from app.cities.registry import canonical_city_id, evaluate_status, get_or_create_city, missing_components
from app.cities.router import route_places
from app.db.models import CityEnrichmentComponent, Destination, EntityAlias, KnowledgeChunk, PlaceVibeProfile, Poi, Vibe
from app.db.session import SessionLocal
from app.geo.opening_hours import parse_google, status_at
from app.main import app
from app.models import Candidate
from app.places.providers.base import PlaceProviderError, PlaceResult, PlaceReview, PlaceSearchProvider, PlaceSearchResponse
from app.places.providers.serpapi_maps import SerpApiPlaceProvider, place_from_item
from app.places.store import upsert_places
from app.policy.recommendation import PolicyContext, exclusion_reason, is_place_of_worship, is_recommendation_excluded
from app.search.serpapi import SearchResponse, SearchResult, SerpApiClient

CENTRE = (30.0, 40.0)


@pytest.fixture(autouse=True)
def no_throttle():
    """The provider throttle is real in production; tests don't need to wait."""
    from app.core.rules import load_rules

    provider = load_rules("city_intelligence")["provider"]
    original = provider["min_interval_s"]
    provider["min_interval_s"] = 0
    yield
    provider["min_interval_s"] = original


@pytest.fixture
def city():
    """A fresh registered city per test (≈6 km extent from its population)."""
    city_id = f"dest-ci-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(Destination(id=city_id, name=f"Ciudad {city_id[-4:]}", region="Test Province", country="Testland", country_code="TL", lat=CENTRE[0], lon=CENTRE[1], coverage_radius_km=5.0, population=1_000_000, timezone="UTC", enrichment_status="NOT_STARTED"))
        db.commit()
        destination = db.get(Destination, city_id)
    yield destination
    with SessionLocal() as db:
        db.execute(delete(Poi).where(Poi.destination_id == city_id))
        db.execute(delete(CityEnrichmentComponent).where(CityEnrichmentComponent.destination_id == city_id))
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.destination_id == city_id))
        db.execute(delete(Destination).where(Destination.id == city_id))
        db.commit()


def place(pid, name, lat=CENTRE[0] + 0.01, lon=CENTRE[1] + 0.01, types=("Museum",), **extra):
    return PlaceResult(provider="fake", external_id=pid, name=name, lat=lat, lon=lon, types=list(types), primary_type=types[0] if types else None, address=extra.pop("address", "1 Main St, Testland"), **extra)


class FakeMaps(PlaceSearchProvider):
    name = "fake_maps"

    def __init__(self, by_query=None, default=(), fail=None, configured=True, details=None):
        self.by_query, self.default, self.fail, self._configured, self._details = by_query or {}, list(default), fail or {}, configured, details or {}
        self.calls = []

    @property
    def configured(self):
        return self._configured

    def search(self, query, *, lat, lon, zoom=13, limit=20):
        self.calls.append(query)
        for fragment, code in self.fail.items():
            if fragment in query:
                raise PlaceProviderError(self.name, code, "boom")
        for fragment, results in self.by_query.items():
            if fragment in query:
                return PlaceSearchResponse(query, list(results))
        return PlaceSearchResponse(query, list(self.default))

    def details(self, found):
        return self._details.get(found.external_id)


def no_knowledge(destination):
    return 0, 0, None


def pois(city_id):
    with SessionLocal() as db:
        return db.scalars(select(Poi).where(Poi.destination_id == city_id)).all()


# ---- city registry ---------------------------------------------------------------------------------

def test_new_city_is_registered_once_with_a_deterministic_id():
    payload = {"name": "Nuevaville", "country": "Testland", "lat": -33.0, "lon": -70.0}
    first, created = get_or_create_city(payload)
    again, created_again = get_or_create_city({**payload, "lat": -33.01})
    try:
        assert created and not created_again and first.id == again.id == canonical_city_id("Nuevaville", "Testland", -33.0, -70.0)
        assert first.enrichment_status == "NOT_STARTED"
    finally:
        with SessionLocal() as db:
            db.execute(delete(EntityAlias).where(EntityAlias.entity_id == first.id))
            db.execute(delete(Destination).where(Destination.id == first.id))
            db.commit()


def test_selecting_a_city_triggers_enrichment_and_selecting_again_does_not_duplicate(city, monkeypatch):
    started = []
    monkeypatch.setattr(city_service, "CITY_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(city_service, "enqueue_enrichment", lambda destination_id: started.append(destination_id) or "job-1")
    monkeypatch.setattr(city_service, "is_running", lambda destination_id: bool(started))
    first = city_service.ensure_city({"destination_id": city.id})
    second = city_service.ensure_city({"destination_id": city.id})
    assert first["enrichment"] == "started" and first["job_id"] == "job-1"
    assert second["enrichment"] == "running" and started == [city.id]  # no second run while one is going


def test_fresh_city_needs_no_enrichment_and_stale_or_failed_parts_are_retried(city):
    run_enrichment(city.id, provider=FakeMaps(default=[place("p1", "City Museum")]), knowledge_collector=lambda d: (1, 1, None))
    with SessionLocal() as db:
        city = db.get(Destination, city.id)
    assert missing_components(city) == [] and evaluate_status(city)["status"] == "READY"
    with SessionLocal() as db:
        row = db.get(CityEnrichmentComponent, f"{city.id}:museums")
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.commit()
    assert missing_components(city) == ["museums"]
    assert evaluate_status(city)["status"] == "PARTIAL"  # an optional part expired; required parts are fresh
    with SessionLocal() as db:
        row = db.get(CityEnrichmentComponent, f"{city.id}:attractions")
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        db.commit()
    assert evaluate_status(city)["status"] == "STALE"


# ---- enrichment: idempotent, partial, deduplicated, bounded ---------------------------------------

def test_enrichment_is_idempotent_and_the_same_place_from_many_queries_is_stored_once(city):
    museum = place("p-museum", "City Museum")
    provider = FakeMaps(default=[museum, place("p-park", "Central Park", types=("Park",))])
    report = run_enrichment(city.id, provider=provider, knowledge_collector=no_knowledge)
    assert report.created == 2 and len(pois(city.id)) == 2  # found by every category query, stored once
    again = run_enrichment(city.id, provider=provider, knowledge_collector=no_knowledge, force=True)
    assert again.created == 0 and len(pois(city.id)) == 2
    stored = {p.external_place_id: p for p in pois(city.id)}
    assert stored["p-museum"].category == "museum" and stored["p-park"].category == "park" and stored["p-museum"].expires_at is not None


def test_a_place_reached_under_another_id_nearby_merges_into_one(city):
    upsert_places(city, [place("id-a", "Old Fort")])
    report = upsert_places(city, [place("id-b", "Old Fort", lat=CENTRE[0] + 0.0105)])  # same name, ~60 m away
    assert report.created == 0 and report.deduplicated == 1 and len(pois(city.id)) == 1


def test_the_same_place_twice_in_one_batch_is_stored_once_with_one_audit_record(city):
    twice = [place("dup", "City Museum", raw={"title": "City Museum"}), place("dup", "City Museum", raw={"title": "City Museum"})]
    report = upsert_places(city, twice)
    assert report.created == 1 and len(pois(city.id)) == 1


def test_a_crashed_run_marks_its_parts_failed_so_they_are_retried(city, monkeypatch):
    import time as time_module

    def crash(*args, **kwargs):
        enrichment_module._save_component(city.id, "museums", "running")
        raise RuntimeError("boom")

    job = enrichment_module.enqueue_enrichment(city.id, runner=crash)
    for _ in range(100):
        if not enrichment_module.is_running(city.id):
            break
        time_module.sleep(0.02)
    assert job and evaluate_status(city)["components"]["museums"]["status"] == "failed"


def test_numbered_places_sharing_an_address_stay_separate(city):
    report = upsert_places(city, [place("t1", "Terminal 1", address="Airport Road, Testland"), place("t2", "Terminal 2", lat=CENTRE[0] + 0.012, address="Airport Road, Testland")])
    assert report.created == 2


def test_results_outside_the_city_or_without_coordinates_are_not_stored(city):
    report = upsert_places(city, [place("far", "Famous Waterfall", lat=CENTRE[0] + 0.5), place("nocoord", "Somewhere", lat=None, lon=None), place("abroad", "Border Market", address="Road 1, 12345 France")])
    assert report.created == 0 and report.rejected == {"outside_boundary": 1, "missing_coordinates": 1, "other_country": 1}


def test_curated_rows_are_linked_but_never_overwritten(city):
    with SessionLocal() as db:
        db.add(Poi(id=f"cur-{city.id}", destination_id=city.id, name="City Museum", normalized_name="city museum", kind="attraction", category="museum", lat=CENTRE[0] + 0.01, lon=CENTRE[1] + 0.01, description="Curated description.", rating=4.9, source="curated pack", data_source_id="pack"))
        db.commit()
    upsert_places(city, [place("p-museum", "City Museum", rating=3.1, description="Provider text.", phone="+1 555")])
    row = pois(city.id)[0]
    assert len(pois(city.id)) == 1 and row.description == "Curated description." and row.rating == 4.9
    assert row.external_place_id == "p-museum" and row.phone == "+1 555"  # identity linked, gaps filled


def test_partial_enrichment_when_one_part_fails_and_stored_data_survives(city):
    provider = FakeMaps(default=[place("p1", "City Museum")], fail={"parks in": "provider_failure", "gardens in": "provider_failure"})
    report = run_enrichment(city.id, provider=provider, knowledge_collector=lambda d: (1, 1, None))
    status = evaluate_status(city)
    assert "parks" in report.failed_components and status["components"]["parks"]["status"] == "failed"
    assert status["status"] == "PARTIAL" and len(pois(city.id)) == 1


def test_quota_or_key_errors_stop_the_run_without_more_requests(city):
    provider = FakeMaps(fail={" in ": "rate_limited"})
    report = run_enrichment(city.id, provider=provider, knowledge_collector=no_knowledge)
    assert report.stopped == "rate_limited" and len(provider.calls) <= 3  # concurrent calls in flight at most


def test_unconfigured_provider_marks_parts_unavailable_and_nothing_breaks(city):
    report = run_enrichment(city.id, provider=FakeMaps(configured=False), knowledge_collector=lambda d: (1, 1, None))
    status = evaluate_status(city)
    assert status["components"]["attractions"]["status"] == "unavailable" and status["components"]["knowledge"]["status"] == "done"
    assert status["status"] == "PARTIAL" and "attractions" in report.failed_components


def test_request_budget_is_respected(city, monkeypatch):
    from app.core import rules as rules_module

    config = json.loads(json.dumps(rules_module.load_rules("city_intelligence")))
    config["provider"]["max_requests_per_run"] = 4
    config["provider"]["min_interval_s"] = 0
    monkeypatch.setattr(enrichment_module, "load_rules", lambda name: config if name == "city_intelligence" else rules_module.load_rules(name))
    provider = FakeMaps(default=[place("p1", "City Museum")])
    run_enrichment(city.id, provider=provider, knowledge_collector=no_knowledge)
    assert len(provider.calls) == 4


def test_place_details_feed_review_vibes_without_touching_facts(city):
    base = place("p-lake", "Quiet Lake", types=("Lake",), rating=4.6)
    detailed = place("p-lake", "Quiet Lake", types=("Lake",), rating=4.6, reviews=[PlaceReview("So peaceful and calm in the morning, lovely scenic views", 5), PlaceReview("Peaceful walk around the lake, very scenic", 5), PlaceReview("Too crowded on weekends", 3)])
    run_enrichment(city.id, ["nature"], provider=FakeMaps(default=[base], details={"p-lake": detailed}), knowledge_collector=no_knowledge)
    poi = pois(city.id)[0]
    with SessionLocal() as db:
        profiles = {db.get(Vibe, row.vibe_id).key: row for row in db.scalars(select(PlaceVibeProfile).where(PlaceVibeProfile.place_id == poi.id)).all()}
    assert profiles["peaceful"].support == 2 and profiles["peaceful"].score > 0.2 and profiles["peaceful"].confidence > 0
    assert poi.rating == 4.6 and poi.category == "lake"


# ---- knowledge documents ---------------------------------------------------------------------------

def _wiki(title, lat, lon, extract="Lead sentence about the city. It is old.\n\n== History ==\nFounded long ago.\n\n== Culture ==\nMusic and dance.", disambiguation=False):
    return {"query": {"pages": [{"title": title, "extract": extract, "fullurl": f"https://en.wikipedia.org/wiki/{title}", "coordinates": [{"lat": lat, "lon": lon}], "pageprops": {"disambiguation": ""} if disambiguation else {}}]}}


def test_city_knowledge_comes_from_the_matching_article_with_attribution(city, monkeypatch):
    pages = {f"{city.name}, Test Province": _wiki("Wrong place", 0.0, 0.0), city.name: _wiki(city.name, CENTRE[0], CENTRE[1])}
    monkeypatch.setattr("app.cities.knowledge.get_json", lambda provider, url, params=None, **kw: pages.get(params["titles"], {"query": {"pages": [{"missing": True}]}}))
    stored, requests, error = collect_knowledge(city)
    assert error is None and requests == 2 and stored == 3  # the far-away same-named article was skipped
    with SessionLocal() as db:
        rows = {r.category: r for r in db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.destination_id == city.id)).all()}
    assert set(rows) == {"overview", "history", "culture"} and rows["history"].kind == "city_kb" and "Wikipedia" in rows["history"].source


def test_disambiguation_pages_are_never_used(city, monkeypatch):
    monkeypatch.setattr("app.cities.knowledge.get_json", lambda provider, url, params=None, **kw: _wiki(params["titles"], CENTRE[0], CENTRE[1], disambiguation=True))
    assert collect_knowledge(city)[0] == 0


# ---- routing: database first ------------------------------------------------------------------------

class CountingWeb(SerpApiClient):
    def __init__(self, results=()):
        super().__init__(api_key="k")
        self.calls, self.results = 0, list(results)

    def search(self, query, **kwargs):
        self.calls += 1
        return SearchResponse(query=query, engine="google_maps", results=list(self.results), retrieved_at=datetime.now(timezone.utc).isoformat())


def _maps_result(pid, name, lat, lon, place_type="Museum"):
    return SearchResult(title=name, url=None, snippet="", source="Google Maps", engine="google_maps", place_id=pid, latitude=lat, longitude=lon, place_type=place_type, rating=4.5, review_count=100)


def _discover(city, web, category=None, require_open=False):
    from app.geo.geo_context import ActiveReference
    from app.services.discovery import DiscoveryRequest, discover

    reference = ActiveReference(origin="active_destination", lat=city.lat, lon=city.lon, label=city.name, radius_km=6.0, semantic="in_destination", destination_id=city.id)
    return discover(DiscoveryRequest(reference=reference, category=category, kinds={"attraction", "activity"} if not category else None, allow_osm=False, require_open=require_open, time_sensitive=require_open), web=web)


def test_prepared_city_answers_from_the_database_without_live_search(city):
    run_enrichment(city.id, provider=FakeMaps(default=[place(f"p{i}", f"Museum {i}", lat=CENTRE[0] + 0.002 * i) for i in range(6)]), knowledge_collector=lambda d: (1, 1, None))
    web = CountingWeb()
    result = _discover(city, web, category="museum")
    assert web.calls == 0 and len(result.candidates) == 6


def test_unprepared_city_goes_live_and_keeps_reusable_places(city):
    web = CountingWeb([_maps_result("live-1", "Harbour Museum", CENTRE[0] + 0.01, CENTRE[1]), _maps_result("live-far", "Distant Museum", CENTRE[0] + 1.0, CENTRE[1])])
    first = _discover(city, web, category="museum")
    assert web.calls == 1 and [c.name for c in first.candidates] == ["Harbour Museum"]
    assert [p.external_place_id for p in pois(city.id)] == ["live-1"]  # stored; the out-of-area one was not
    assert first.candidates[0].id.startswith("gm-")  # answered with the stored row's stable id


def test_time_sensitive_questions_are_hybrid_and_thin_coverage_goes_live():
    assert route_places(destination_id=None, stored_count=10, category_requested=False, time_sensitive=True, live_available=True, legacy_minimum=5).route == "hybrid"
    assert route_places(destination_id=None, stored_count=1, category_requested=False, time_sensitive=False, live_available=True, legacy_minimum=5).route == "live_places"
    assert route_places(destination_id=None, stored_count=1, category_requested=False, time_sensitive=False, live_available=False, legacy_minimum=5).route == "database"


def test_an_unknown_place_is_discovered_live_and_stored(city):
    from app.geo.geo_context import ActiveReference
    from app.services.search import search

    reference = ActiveReference(origin="active_destination", lat=city.lat, lon=city.lon, label=city.name, radius_km=6.0, semantic="in_destination", destination_id=city.id)
    web = CountingWeb([_maps_result("live-cafe", "Blue Door Cafe", CENTRE[0] + 0.003, CENTRE[1], place_type="Cafe")])
    search("Blue Door Cafe", reference=reference, user_point=None, profile={}, web=web)
    assert web.calls == 1 and [p.name for p in pois(city.id)] == ["Blue Door Cafe"]


# ---- SerpApi Google Maps normalisation ------------------------------------------------------------

def test_serpapi_local_result_is_normalised():
    item = {"title": "Lalbagh Botanical Garden", "place_id": "ChIJ123", "data_id": "0x1:0x2", "gps_coordinates": {"latitude": 12.95, "longitude": 77.58}, "rating": 4.5, "reviews": 120345,
            "price": "₹₹", "type": "Botanical garden", "types": ["Botanical garden", "Park"], "address": "Mavalli, Bengaluru, Karnataka 560004, India",
            "operating_hours": {"monday": "6 AM–7 PM", "tuesday": "6 AM–7 PM", "wednesday": "6 AM–7 PM", "thursday": "6 AM–7 PM", "friday": "6 AM–7 PM", "saturday": "6 AM–7 PM", "sunday": "6 AM–7 PM"},
            "open_state": "Open ⋅ Closes 7 PM", "website": "https://example.org", "thumbnail": "https://img.example/x.jpg"}
    found = place_from_item(item)
    assert found.external_id == "ChIJ123" and found.review_count == 120345 and found.hours["monday"] == "6 AM–7 PM" and found.images == ["https://img.example/x.jpg"]
    from app.places.normalize import poi_values

    values = poi_values(found)
    assert values["category"] == "park" and values["price_level"] == 2 and json.loads(values["opening_hours"])["weekly"]["mon"] == [["06:00", "19:00"]]
    assert values["data_source_id"] == "google_maps" and "place_id:ChIJ123" in values["source_url"]


def test_provider_uses_maps_search_and_place_parameters(monkeypatch):
    calls = []

    class Recording(SerpApiClient):
        def fetch_json(self, params, ttl_s):
            calls.append(params)
            return ({"local_results": [{"title": "X", "place_id": "P1", "gps_coordinates": {"latitude": 1, "longitude": 2}}]} if params.get("type") == "search" else {"place_results": {"title": "X", "place_id": "P1"}}), False

    provider = SerpApiPlaceProvider(Recording(api_key="k"))
    found = provider.search("museums in X", lat=1.0, lon=2.0, zoom=13).results[0]
    provider.details(found)
    assert calls[0]["engine"] == "google_maps" and calls[0]["type"] == "search" and calls[0]["ll"] == "@1.000000,2.000000,13z"
    assert calls[1] == {"engine": "google_maps", "hl": "en", "place_id": "P1"}


def test_permanently_closed_places_are_flagged():
    assert place_from_item({"title": "Old Cinema", "place_id": "C1", "open_state": "Permanently closed"}).permanently_closed


def test_google_hours_handle_24h_and_overnight():
    hours = parse_google({"friday": "6 PM–2 AM", "saturday": "Closed", "sunday": "Open 24 hours", "monday": "9 AM–5 PM", "tuesday": "9 AM–5 PM", "wednesday": "9 AM–5 PM", "thursday": "9 AM–5 PM"})
    assert status_at(hours, datetime(2026, 9, 25, 23, 0))["status"] == "open"  # Fri 23:00
    assert status_at(hours, datetime(2026, 9, 26, 1, 0))["status"] == "closed"  # Sat 01:00: Saturday is closed all day
    assert status_at(hours, datetime(2026, 9, 27, 3, 0))["status"] == "open"  # Sun open 24 h


# ---- recommendation policy (religion-neutral) -----------------------------------------------------

def _candidate(name, category, types=(), **extra):
    return Candidate(id=name, name=name, category=category, kind="attraction", lat=CENTRE[0], lon=CENTRE[1], provider_types=list(types), **extra)


WORSHIP = [("Hindu temple", "temple", ["Hindu temple"]), ("Church", "monument", ["Church"]), ("Cathedral", None, ["CATHEDRAL"]), ("Mosque", None, ["Mosque"]),
           ("Masjid", None, ["masjid"]), ("Gurdwara", None, ["Gurdwara"]), ("Synagogue", None, ["Synagogue"]), ("Buddhist monastery", None, ["Buddhist temple", "Monastery"])]


@pytest.mark.parametrize("name, category, types", WORSHIP)
def test_places_of_worship_are_treated_identically_whatever_the_faith(name, category, types):
    candidate = _candidate(name, category, types)
    assert is_place_of_worship(candidate)
    assert not is_recommendation_excluded(candidate)  # never excluded by default
    assert exclusion_reason(candidate, PolicyContext(exclude_places_of_worship=True)) == "place_of_worship_excluded_by_traveller"


def test_policy_never_blocks_an_explicit_factual_question_and_ignores_unrelated_places():
    assert not is_recommendation_excluded(_candidate("Mosque", None, ["Mosque"]), PolicyContext(explicit=True, exclude_places_of_worship=True))
    assert not is_place_of_worship(_candidate("City Museum", "museum", ["Museum"]))


def test_closed_and_configured_exclusions_apply_everywhere(monkeypatch):
    from app.core import rules as rules_module

    assert exclusion_reason(_candidate("Old Cinema", "activity", open_detail={"permanently_closed": True})) == "permanently_closed"
    policy = json.loads(json.dumps(rules_module.load_rules("recommendation_policy")))
    policy["excluded_categories"] = ["nightlife"]
    monkeypatch.setattr("app.policy.recommendation.load_rules", lambda name: policy if name == "recommendation_policy" else rules_module.load_rules(name))
    assert exclusion_reason(_candidate("Club", "nightlife")) == "excluded_category"


def test_ranking_and_city_listings_apply_the_policy(city):
    from app.ranking.ranker import RankRequest, rank

    rows = [_candidate("Harbour Church", "temple", ["Church"]), _candidate("City Museum", "museum", ["Museum"]), _candidate("Old Cinema", "activity", open_detail={"permanently_closed": True})]
    ranked = rank(rows, RankRequest(profile_name="discovery", user={"exclude_places_of_worship": True})).ranked
    assert [c.name for c in ranked] == ["City Museum"]
    assert [c.name for c in rank(rows[:1], RankRequest(profile_name="lookup", user={"exclude_places_of_worship": True})).ranked] == ["Harbour Church"]  # a named look-up is factual
    upsert_places(city, [place("w1", "Harbour Temple", types=("Hindu temple",)), place("m1", "City Museum", lat=CENTRE[0] + 0.02)])
    listing = city_service.city_places(city.id, profile={"exclude_places_of_worship": True})
    assert [i["name"] for i in listing["items"]] == ["City Museum"] and listing["excluded_by_policy"] == 1
    assert len(city_service.city_places(city.id, profile={})["items"]) == 2


# ---- API ----------------------------------------------------------------------------------------------

def test_city_api_ensure_status_knowledge_places_and_enrich_guard(city):
    client = TestClient(app)
    ensured = client.post("/api/destinations/ensure", json={"destination_id": city.id}).json()
    assert ensured["city"]["id"] == city.id and ensured["enrichment"] == "disabled" and ensured["status"]["status"] == "NOT_STARTED"
    upsert_places(city, [place("m1", "City Museum")])
    assert client.get(f"/api/destinations/{city.id}/status").json()["status"]["place_count"] == 1
    assert client.get(f"/api/destinations/{city.id}/places").json()["items"][0]["name"] == "City Museum"
    assert client.get(f"/api/destinations/{city.id}/knowledge").json() == {"items": []}
    assert client.post(f"/api/destinations/{city.id}/enrich", json={"force": True}).status_code == 403
    assert client.post("/api/destinations/ensure", json={"name": "", "lat": 999}).status_code == 422
    assert client.get("/api/destinations/nope/status").status_code == 404


@pytest.mark.parametrize("question, intent, place", [
    ("tell me about the history of this city", "DESTINATION_KNOWLEDGE", None),
    ("tell me about the food in the city", "DESTINATION_KNOWLEDGE", None),
    ("things to do in Mexico City", "ACTIVITY_DISCOVERY", "Mexico City"),
])
def test_questions_about_the_current_city_are_not_place_lookups(question, intent, place):
    from app.query.parser import parse_query

    parsed = parse_query(question)
    assert parsed.intent.value == intent and parsed.place_mention == place and parsed.entity_mention is None
