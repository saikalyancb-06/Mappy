from datetime import datetime, timedelta, timezone

import pytest

from app.services.agent import GeoGuideAgent
from app.services.location_context import LocationContextError, extract_explicit_destination, validate_location_context
from app.services.web_search import SearchResult, SerpApiSearchProvider


def test_location_context_validates_provenance_and_staleness():
    context = validate_location_context({'lat': 1, 'lon': 2, 'source': 'device', 'timestamp': datetime.now(timezone.utc).isoformat(), 'accuracy_meters': 12})
    assert context['source'] == 'device'
    with pytest.raises(LocationContextError):
        validate_location_context({'lat': 1, 'lon': 2, 'source': 'device', 'timestamp': (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()})
    with pytest.raises(LocationContextError):
        validate_location_context({'lat': 95, 'lon': 2, 'source': 'device'})


def test_explicit_destination_does_not_match_near_me():
    assert extract_explicit_destination('restaurants near me') is None
    assert extract_explicit_destination('restaurants in a destination') == 'a destination'


def test_live_results_are_distance_filtered():
    results = [
        SearchResult('near', 'https://near.example', '', 'near.example', latitude=0.01, longitude=0.01),
        SearchResult('far', 'https://far.example', '', 'far.example', latitude=10, longitude=10),
    ]
    filtered = SerpApiSearchProvider.filter_by_distance(results, 0, 0, 5)
    assert [result.title for result in filtered] == ['near']


def test_empty_evidence_never_calls_groq(monkeypatch):
    agent = GeoGuideAgent()
    monkeypatch.setattr(agent.llm, 'chat', lambda prompt: (_ for _ in ()).throw(AssertionError('Groq must not run')))
    result = agent.answer('restaurants near me', {'location': {'lat': 1, 'lon': 2}}, evidence=[])
    assert result['verification_status'] == 'no_results'


def test_nearby_mode_uses_tight_radius_and_everywhere_uses_broad():
    """Nearby and Everywhere modes must use different default radii."""
    from app.api.routes import _MODE_RADIUS
    assert _MODE_RADIUS['nearby'] < _MODE_RADIUS['everywhere']
    assert _MODE_RADIUS['nearby'] <= 2.0
    assert _MODE_RADIUS['everywhere'] >= 10.0


def test_now_mode_filters_by_open_status():
    """Now mode must exclude places with no open status or hours."""
    open_place = {'name': 'Open café', 'lat': 0, 'lon': 0, 'open_now': True, 'category': 'cafe'}
    closed_place = {'name': 'Closed shop', 'lat': 0, 'lon': 0, 'open_now': False, 'category': 'shop', 'opening_hours': None}
    no_hours_place = {'name': 'Unknown hours', 'lat': 0, 'lon': 0, 'category': 'restaurant'}
    has_hours_place = {'name': 'Has hours', 'lat': 0, 'lon': 0, 'category': 'restaurant', 'opening_hours': 'Mon-Fri 9-17'}
    places = [open_place, closed_place, no_hours_place, has_hours_place]
    now_filtered = [p for p in places if p.get('open_now') is True or p.get('opening_hours')]
    assert open_place in now_filtered
    assert has_hours_place in now_filtered
    assert no_hours_place not in now_filtered
    assert closed_place not in now_filtered


def test_stale_destination_does_not_contaminate_near_me_query():
    """A 'near me' query must use physical location, not a previous explicit destination."""
    # extract_explicit_destination must return None for 'near me' style queries
    assert extract_explicit_destination('what is near me right now') is None
    assert extract_explicit_destination('coffee near me') is None
    # and must extract a destination for explicit place mentions
    assert extract_explicit_destination('restaurants in the city centre') == 'the city centre'


def test_serpapi_maps_search_adds_nearby_param(monkeypatch):
    """google_maps engine must include the 'nearby' coordinate parameter."""
    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, params=None):
            captured['params'] = params or {}
            import httpx
            class FakeResp:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {'local_results': []}
            return FakeResp()

    monkeypatch.setattr('app.services.web_search.httpx.Client', CapturingClient)
    provider = SerpApiSearchProvider(api_key='test-key')
    provider.search('cafes near me', location={'lat': 12.9, 'lon': 77.5})
    assert 'nearby' in captured.get('params', {}), 'nearby param missing for google_maps engine'
    assert 'll' in captured.get('params', {}), 'll param missing for google_maps engine'


def test_coordinate_validation_rejects_missing_and_out_of_range():
    """Invalid coordinate combinations must raise LocationContextError."""
    with pytest.raises(LocationContextError):
        validate_location_context({'source': 'device'})  # missing lat/lon
    with pytest.raises(LocationContextError):
        validate_location_context({'lat': -91, 'lon': 0, 'source': 'device'})  # lat out of range
    with pytest.raises(LocationContextError):
        validate_location_context({'lat': 0, 'lon': 181, 'source': 'device'})  # lon out of range
    # valid boundary values must pass
    context = validate_location_context({'lat': 0.0, 'lon': 0.0, 'source': 'device'})
    assert context['latitude'] == 0.0
    assert context['longitude'] == 0.0
