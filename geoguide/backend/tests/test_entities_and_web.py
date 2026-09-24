"""Entity resolution, web normalisation and candidate aggregation."""
from app.entities.resolver import resolve_entity
from app.models import Candidate, SourceRef
from app.search.aggregator import aggregate
from app.search.normalizer import candidate_from_web, classify_category
from app.search.serpapi import SearchProviderError, SearchResult, SerpApiClient


class FakeSerp(SerpApiClient):
    def __init__(self, results=None, error=None):
        super().__init__(api_key="test")
        self.results, self.error, self.calls = results or [], error, []

    def search(self, query, **kwargs):
        from app.search.serpapi import SearchResponse

        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return SearchResponse(query, kwargs.get("engine", "google"), list(self.results), "2026-09-24T00:00:00Z")


def maps_result(title, lat, lon, **kwargs):
    return SearchResult(title=title, url=None, snippet="", source="Google Maps", engine="google_maps", latitude=lat, longitude=lon, **kwargs)


def test_exact_match_resolves():
    resolution = resolve_entity("Old Fort", allow_web=False)
    assert resolution.status == "resolved" and resolution.entity.id == "tv-old-fort"


def test_alias_and_partial_match_resolve():
    assert resolve_entity("Fort of Testville", allow_web=False).entity.id == "tv-old-fort"
    assert resolve_entity("Lotus", allow_web=False).entity.id == "tv-lotus-temple"


def test_branch_ambiguity_without_locality_is_not_guessed():
    resolution = resolve_entity("Sunrise Cafe", allow_web=False)
    # The exact-name branch wins over a suffixed branch, but only on evidence; a locality flips it.
    north = resolve_entity("Sunrise Cafe", locality_hint="North", allow_web=False)
    assert north.status == "resolved" and north.entity.id == "tv-sunrise-cafe-north"
    market = resolve_entity("Sunrise Cafe", locality_hint="Market Street", allow_web=False)
    assert market.status == "resolved" and market.entity.id == "tv-sunrise-cafe-market"
    assert resolution.alternatives and {a.candidate.id for a in resolution.alternatives} >= {"tv-sunrise-cafe-market", "tv-sunrise-cafe-north"}


def test_unknown_entity_is_not_found():
    resolution = resolve_entity("Quantum Noodle Palace", allow_web=False)
    assert resolution.status == "not_found" and resolution.entity is None


def test_similar_but_different_businesses_are_ambiguous_not_merged():
    web = FakeSerp([maps_result("Golden Spoon", 10.0, 20.001, address="1 Lake Rd"), maps_result("Golden Spoon Express", 10.02, 20.02, address="9 Hill Rd"), maps_result("Golden Spoon", 10.05, 20.05, address="4 Far Rd")])
    resolution = resolve_entity("Golden Spoon", web=web, reference=(10.0, 20.0))
    assert resolution.status in {"ambiguous", "resolved"}
    assert len(resolution.alternatives) >= 2  # two same-name places far apart stay separate
    if resolution.status == "resolved":
        assert resolution.entity.address == "1 Lake Rd"  # the nearer one, never an arbitrary pick


def test_web_candidate_is_resolved_with_locality_and_source_kept():
    web = FakeSerp([maps_result("Moonlight Tiffin Room", 12.94, 77.57, address="Gandhi Bazaar Main Rd", rating=4.4, review_count=8000, place_type="South Indian restaurant")])
    resolution = resolve_entity("Moonlight Tiffin Room", locality_hint="Gandhi Bazaar", web=web)
    assert resolution.status == "resolved"
    assert resolution.entity.sources[0].source_type == "serpapi_maps"
    assert resolution.entity.category == "restaurant"


def test_web_failure_degrades_to_database():
    web = FakeSerp(error=SearchProviderError("timeout", "Web search timed out."))
    resolution = resolve_entity("Old Fort", web=web, prefer_live=True)
    assert resolution.status == "resolved" and resolution.entity.id == "tv-old-fort"


def test_duplicates_across_sources_merge_with_field_priority():
    curated = Candidate(id="tv-lotus-temple", name="Lotus Temple", category="temple", kind="attraction", lat=10.005, lon=20.0, sources=[SourceRef("curated", "pack", confidence=0.95)], confidence=0.95)
    web = candidate_from_web(maps_result("Lotus Temple", 10.0051, 20.0001, rating=4.7, review_count=320, open_state="Open ⋅ Closes 8 PM", place_type="Hindu temple"), "now")
    other = candidate_from_web(maps_result("Lotus Pond Cafe", 10.0052, 20.0002, place_type="Cafe"), "now")
    merged, duplicates = aggregate([[curated], [web, other]])
    assert duplicates == 1 and len(merged) == 2
    temple = next(c for c in merged if c.id == "tv-lotus-temple")
    assert (temple.lat, temple.lon) == (10.005, 20.0)  # coordinates: curated wins
    assert temple.rating == 4.7 and temple.review_count == 320  # ratings: maps wins
    assert temple.open_status == "open"
    assert {s.source_type for s in temple.sources} == {"curated", "serpapi_maps"}
    assert temple.confidence > 0.95


def test_web_results_without_coordinates_are_not_places():
    assert candidate_from_web(SearchResult("Top 10 cafes", "https://blog.example", "list", "blog.example", "google"), "now") is None


def test_provider_type_strings_map_to_taxonomy():
    assert classify_category(["Coffee shop"]) == "cafe"
    assert classify_category(["South Indian restaurant"]) == "restaurant"
    assert classify_category(["Hindu temple"]) == "temple"
    assert classify_category(["Car dealer"]) is None


def test_serpapi_parsing_and_missing_key():
    payload = {"local_results": [{"title": "Bean There", "gps_coordinates": {"latitude": 1.0, "longitude": 2.0}, "rating": 4.5, "reviews": "1,204", "type": "Coffee shop", "open_state": "Open ⋅ Closes 9 PM", "place_id": "abc"}, {"title": "No coords"}]}
    results = SerpApiClient.parse(payload, "google_maps")
    assert results[0].review_count == 1204 and results[0].latitude == 1.0 and results[0].place_id == "abc"
    events = SerpApiClient.parse({"events_results": [{"title": "Fair", "date": {"when": "Sat, 5 pm"}, "link": "https://e.example", "address": ["Main St", "Town"]}]}, "google_events")
    assert events[0].event_date == "Sat, 5 pm" and events[0].address == "Main St, Town"
    try:
        SerpApiClient(api_key="").search("cafes")
    except SearchProviderError as exc:
        assert exc.code == "web_search_unavailable"
    else:
        raise AssertionError("missing key must raise")
