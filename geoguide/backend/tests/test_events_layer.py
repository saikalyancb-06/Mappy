"""Event discovery: providers, date/place validation, de-duplication, confidence, freshness, ranking, failures."""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.dates import parse_range, single, weekend_of
from app.events.engine import build_query, find_events
from app.events.model import EventSource, NormalisedEvent, zone
from app.events.providers.base import EventProvider, ProviderResult
from app.events.providers.ticketmaster import TicketmasterProvider
from app.events.providers.web import WebEventProvider, build_queries
from app.events.service import city_events
from app.events.taxonomy import categorise, price_from_text
from app.geo.city import resolve_city
from app.main import app
from app.search.serpapi import SearchResponse, SearchResult, SerpApiClient

UTC = zone("UTC")
TODAY = datetime.now(UTC).date()
TOMORROW = TODAY + timedelta(days=1)


def _testville():
    return resolve_city(destination_id="dest-testville")[0]


def ev(title, day=TOMORROW, at=None, *, provider="fake", kind="web_platform", reliability=0.7, lat=10.001, lon=20.001, venue="Town Hall", url=None, category=None, price="unknown", retrieved=None, end=None, source_id=None):
    start = datetime.combine(day, at or time(0, 0), tzinfo=UTC)
    cat, cats, typ = categorise(title)
    source = EventSource(provider=provider, name=provider.title(), kind=kind, reliability=reliability, source_event_id=source_id or f"{provider}-{title}", url=url or f"https://{provider}.example/{title.replace(' ', '-')}", retrieved_at=retrieved or datetime.now(timezone.utc))
    return NormalisedEvent(title=title, start=start, end=end, timezone="UTC", time_known=at is not None, source=source, category=category or cat, categories=[category] if category else cats, type=typ,
                           venue_name=venue, lat=lat, lon=lon, city="Testville", event_url=source.url, price_kind=price, price_min="0.00" if price == "free" else None)


class Fake(EventProvider):
    def __init__(self, name, events=(), fail=False):
        self.name, self.label, self.items, self.fail = name, f"Fake {name}", list(events), fail

    def search_events(self, query):
        if self.fail:
            raise RuntimeError("boom")
        return ProviderResult(self.name, self.label, events=list(self.items), raw_count=len(self.items))


def run(window, *providers, **kwargs):
    query = build_query(_testville(), window, **kwargs)
    return find_events(query, providers=list(providers))


# ---- date intelligence -------------------------------------------------------------------------

def test_tonight_only_keeps_evening_events_and_all_day_ones():
    window = parse_range("what can I do tonight", TODAY)
    early, late, all_day = ev("Morning Yoga Class", TODAY, time(6, 0), end=datetime.combine(TODAY, time(7, 0), tzinfo=UTC)), ev("Jazz Night", TODAY, time(21, 0)), ev("Street Food Week", TODAY)
    names = [e.title for e in run(window, Fake("f", [early, late, all_day]))["events"]]
    assert "Jazz Night" in names and "Street Food Week" in names and "Morning Yoga Class" not in names


def test_weekend_and_next_weekend_ranges():
    weekend = weekend_of(TODAY)
    inside, outside = ev("Weekend Craft Mela", weekend.end, time(11, 0)), ev("Midweek Talk", weekend.end + timedelta(days=3), time(18, 0))
    names = [e.title for e in run(weekend, Fake("f", [inside, outside]))["events"]]
    assert names == ["Weekend Craft Mela"]


# ---- geography: destination vs near me -------------------------------------------------------

def test_destination_events_even_when_the_traveller_is_elsewhere():
    far_user = (30.0, 40.0)  # physically in another city
    result = run(single(TOMORROW), Fake("f", [ev("Harbour Concert")]), user_point=far_user)
    assert [e.title for e in result["events"]] == ["Harbour Concert"]
    near = run(single(TOMORROW), Fake("f", [ev("Harbour Concert")]), user_point=far_user, mode="near_me")
    assert near["events"] == [] and near["counts"]["outside_area"] == 1


def test_near_me_uses_distance_not_city_names():
    close, far = ev("Corner Gig", lat=10.02, lon=20.02), ev("Distant Gig", lat=10.4, lon=20.4)
    result = run(single(TOMORROW), Fake("f", [close, far]), user_point=(10.02, 20.021), mode="near_me", radius_km=5)
    assert [e.title for e in result["events"]] == ["Corner Gig"] and result["events"][0].distance_km < 1


def test_missing_coordinates_keep_city_events_and_link_known_venues():
    event = ev("Museum Late", lat=None, lon=None, venue="Testville Museum")
    result = run(single(TOMORROW), Fake("f", [event]))
    kept = result["events"][0]
    assert kept.venue_place_id == "tv-museum" and kept.lat is not None  # coordinates inherited from the stored place


# ---- de-duplication ------------------------------------------------------------------------------

def test_same_event_from_two_providers_is_merged_and_corroborated():
    a = ev("Monsoon Music Nights 2026", TOMORROW, time(19, 0), provider="ticketmaster", kind="ticketmaster", reliability=0.9)
    b = ev("Monsoon Music Nights", TOMORROW, time(19, 0), provider="google_events", kind="google_events", reliability=0.65, lat=None, lon=None, venue="Town Hall")
    result = run(single(TOMORROW), Fake("ticketmaster", [a]), Fake("google_events", [b]))
    assert len(result["events"]) == 1 and result["counts"]["duplicates"] == 1
    merged = result["events"][0]
    assert len(merged.sources) == 2 and merged.sources[0].provider == "ticketmaster" and "Reported by 2 sources" in merged.confidence_notes


def test_similar_names_at_different_places_are_not_merged():
    a = ev("Jazz Night", TOMORROW, time(20, 0), lat=10.001, lon=20.001, venue="Blue Bar")
    b = ev("Jazz Night", TOMORROW, time(20, 0), provider="other", lat=10.08, lon=20.08, venue="Harbour Club")
    assert len(run(single(TOMORROW), Fake("f", [a]), Fake("other", [b]))["events"]) == 2


# ---- freshness & confidence ------------------------------------------------------------------------

def test_stale_listings_are_flagged_and_expired_ones_removed():
    stale = ev("Old Listing Expo", retrieved=datetime.now(timezone.utc) - timedelta(days=2))
    now = datetime.now(UTC)
    ended = ev("Finished Talk", TODAY, (now - timedelta(hours=3)).time().replace(microsecond=0), end=now - timedelta(hours=1))
    result = run(parse_range("upcoming", TODAY), Fake("f", [stale, ended]))
    names = {e.title: e for e in result["events"]}
    assert "Finished Talk" not in names
    assert names["Old Listing Expo"].freshness == "stale" and "Listing not re-checked recently" in names["Old Listing Expo"].confidence_notes


def test_official_sources_are_more_confident_than_aggregators():
    official = ev("City Heritage Walk", kind="web_authoritative", reliability=0.85)
    aggregator = ev("River Boat Race", kind="web_aggregator", reliability=0.4, venue=None, lat=None, lon=None)
    result = {e.title: e for e in run(single(TOMORROW), Fake("f", [official, aggregator]))["events"]}
    assert result["City Heritage Walk"].confidence_label == "high"
    assert result["River Boat Race"].confidence_label == "low" and result["City Heritage Walk"].score > result["River Boat Race"].score


# ---- filters: festivals, categories, free ------------------------------------------------------------

def test_festival_category_and_free_filters():
    events = [ev("Kite Festival", price="free"), ev("Symphony Concert", price="unknown"), ev("Food Truck Fair", price="free")]
    assert [e.title for e in run(single(TOMORROW), Fake("f", events), festival_only=True)["events"]] == ["Kite Festival"]
    assert [e.title for e in run(single(TOMORROW), Fake("f", events), categories=["music"])["events"]] == ["Symphony Concert"]
    assert {e.title for e in run(single(TOMORROW), Fake("f", events), free_only=True)["events"]} == {"Kite Festival", "Food Truck Fair"}


@pytest.mark.parametrize("text, kind", [("Free entry for all", "free"), ("Tickets from ₹499", "paid"), ("Pay what you want", "donation"), ("Bring friends", "unknown")])
def test_price_classification(text, kind):
    assert price_from_text(text)[0] == kind


# ---- failures, fallback, empty ------------------------------------------------------------------

def test_provider_failure_does_not_stop_the_others():
    result = run(single(TOMORROW), Fake("broken", fail=True), Fake("ok", [ev("Rooftop Comedy Night", TOMORROW, time(20, 0))]))
    statuses = {r.provider: r.status for r in result["providers"]}
    assert statuses == {"broken": "error", "ok": "ok"} and [e.title for e in result["events"]] == ["Rooftop Comedy Night"]


def test_no_results_stay_empty():
    result = city_events(_testville(), single(TODAY + timedelta(days=200)), providers=[Fake("empty")])
    assert result["events"] == [] and result["event_status"] == "no_verified_events_found" and result["message"].startswith("No verified events found for Testville")


class FakeWeb(SerpApiClient):
    def __init__(self, results):
        super().__init__(api_key="test")
        self.results, self.queries = results, []

    def search(self, query, **kwargs):
        self.queries.append(query)
        return SearchResponse(query, kwargs.get("engine", "google"), list(self.results), datetime.now(timezone.utc).isoformat())


def test_web_fallback_validates_and_prefers_authoritative_pages():
    day = TOMORROW.strftime("%B ") + str(TOMORROW.day)
    web = FakeWeb([
        SearchResult(title=f"Testville Lantern Walk — {day}", url="https://tourism.testville.gov.example/lantern-walk", snippet=f"Join the lantern walk at Riverside Ghat on {day}, 6 PM. Free entry for all.", source="gov", engine="google"),
        SearchResult(title="Top 10 events in Testville this weekend", url="https://listicle.example/top10", snippet=f"{day}: lots to do.", source="x", engine="google"),
        SearchResult(title="Testville Food Walk", url="https://blog.example/food", snippet="A great walk sometime soon.", source="x", engine="google"),
    ])
    provider = WebEventProvider(web)
    result = find_events(build_query(_testville(), single(TOMORROW)), providers=[Fake("empty"), provider])
    web_result = next(r for r in result["providers"] if r.provider == "web")
    assert web_result.dropped == {"not_an_event": 1, "no_date": 1}
    event = result["events"][0]
    assert event.title.startswith("Testville Lantern Walk") and event.price_kind == "free" and event.time_known and event.start.hour == 18
    assert event.sources[0].kind == "web_authoritative" and event.venue_name == "Riverside Ghat"
    assert len(web.queries) <= 3 and all("Testville" in q for q in web.queries)


def test_web_queries_are_generated_from_intent_dates_and_season():
    queries = build_queries(build_query(_testville(), parse_range("in October", date(2026, 9, 24)), categories=["music"], text="jazz"))
    assert queries[0].startswith("jazz Testville") and any("music in Testville" in q for q in queries)
    festival = build_queries(build_query(_testville(), single(TOMORROW), festival_only=True))
    assert any("festival" in q.lower() for q in festival)


# ---- ranking with vibes -------------------------------------------------------------------------

def test_vibe_preferences_shift_event_ranking():
    from app.feedback.service import FeedbackInput, submit_feedback
    from app.feedback.vocabulary import sync_vocabulary

    sync_vocabulary()
    for place in ("tv-old-fort", "tv-sunrise-cafe-market", "tv-spice-kitchen"):
        submit_feedback(FeedbackInput(place_id=place, overall_rating=5, vibes=["lively", "youthful", "social"], custom_vibes=[], liked_aspects=[], disliked_aspects=[]), "party-person")
        submit_feedback(FeedbackInput(place_id=place, overall_rating=5, vibes=["cultural", "calm"], custom_vibes=[], liked_aspects=[], disliked_aspects=[]), "museum-person")
    events = [ev("Indie Music Gig", TOMORROW, time(20, 0)), ev("Heritage Exhibition", TOMORROW, time(20, 0))]
    party = [e.title for e in run(single(TOMORROW), Fake("f", events), user={"user_id": "party-person"})["events"]]
    calm = [e.title for e in run(single(TOMORROW), Fake("f", [ev("Indie Music Gig", TOMORROW, time(20, 0)), ev("Heritage Exhibition", TOMORROW, time(20, 0))]), user={"user_id": "museum-person"})["events"]]
    assert party[0] == "Indie Music Gig" and calm[0] == "Heritage Exhibition"


# ---- Ticketmaster mapping ------------------------------------------------------------------------

def test_ticketmaster_uses_geo_date_filters_and_normalises(monkeypatch):
    seen = {}

    def fake_get_json(provider, url, params=None, **kwargs):
        seen.update(params or {})
        day = TOMORROW.isoformat()
        return {"page": {"totalPages": 1}, "_embedded": {"events": [
            {"id": "tm1", "name": "Big Arena Show", "url": "https://tickets.example/tm1", "images": [{"url": "https://img.example/a.jpg", "width": 1024}],
             "dates": {"start": {"localDate": day, "localTime": "19:30:00"}, "timezone": "UTC", "status": {"code": "onsale"}},
             "classifications": [{"segment": {"name": "Music"}}], "priceRanges": [{"min": 499, "max": 1500, "currency": "INR"}],
             "_embedded": {"venues": [{"name": "Testville Arena", "location": {"latitude": "10.002", "longitude": "20.002"}, "city": {"name": "Testville"}}]}},
            {"id": "tm2", "name": "Cancelled Show", "dates": {"start": {"localDate": day}, "status": {"code": "cancelled"}}},
        ]}}

    monkeypatch.setattr("app.events.providers.ticketmaster.get_json", fake_get_json)
    provider = TicketmasterProvider(api_key="k", url="https://tm.example/discovery/v2/events.json")
    result = provider.search_events(build_query(_testville(), single(TOMORROW), categories=["music"]))
    assert seen["latlong"] == "10.00000,20.00000" and seen["unit"] == "km" and seen["classificationName"] == "Music" and seen["startDateTime"].endswith("Z")
    assert result.dropped == {"cancelled": 1}
    show = result.events[0]
    assert show.category == "music" and show.start.hour == 19 and show.price_min == "499.00" and show.image_url and show.lat == 10.002
    assert TicketmasterProvider(api_key="").search_events(build_query(_testville(), single(TOMORROW))).status == "not_configured"


# ---- API --------------------------------------------------------------------------------------------

def test_events_api_and_overview(gps):
    with TestClient(app) as client:
        assert client.get("/api/events", params={"destination_id": "dest-testville", "near": "me"}).status_code == 422
        providers = client.get("/api/events/providers").json()["providers"]
        assert {p["provider"] for p in providers} >= {"stored", "ticketmaster", "google_events", "web"} and all("key" not in str(p).lower() or "TICKETMASTER_API_KEY" in str(p) for p in providers)
        overview = client.get("/api/events/overview", params={"destination_id": "dest-testville"}).json()
        assert all(tab["count"] > 0 for tab in overview["tabs"])  # only tabs that actually have events
        tonight = client.get("/api/events", params={"destination_id": "dest-testville", "when": "tonight"}).json()
        assert tonight["range"]["kind"] == "tonight"


def test_web_fallback_runs_when_structured_results_do_not_match_the_request():
    class CountingWeb(WebEventProvider):
        calls = 0

        def search_events(self, query):
            CountingWeb.calls += 1
            return ProviderResult("web", "web")

    many_food = [ev(f"Food Stall {i}", category="food") for i in range(6)]
    run(single(TOMORROW), Fake("f", many_food), CountingWeb(SerpApiClient(api_key="k")), categories=["music"])
    assert CountingWeb.calls == 1  # six events, none of them music → look further
    run(single(TOMORROW), Fake("f", many_food), CountingWeb(SerpApiClient(api_key="k")), categories=["food"])
    assert CountingWeb.calls == 1  # enough matching events → no web search
