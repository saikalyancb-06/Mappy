"""City-and-date context: city resolution, date-native events, weather per date, tips and the three judge states.

The rule under test throughout: events are scoped to a city and a date range
(never a radius), and zero verified events stays zero all the way to the answer.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.dates import parse_range, single, weekend_of
from app.db.models import EventFestival, WeatherDaily
from app.db.session import SessionLocal
from app.events.live import listing_dates, refresh_live_events
from app.events.service import NONE, city_events
from app.events.store import classify
from app.geo.city import resolve_city
from app.llm.evidence import EvidenceBuilder
from app.llm.generator import generate
from app.llm.prompts import AnswerContext
from app.llm.validator import validate
from app.main import app
from app.search.serpapi import SearchResponse, SearchResult, SerpApiClient
from app.weather.open_meteo import weather_on
from tests.test_generation_and_planning import ScriptedLLM

TODAY = datetime.now(timezone.utc).date()  # Testville's timezone is UTC
FEST_START, FEST_END = TODAY + timedelta(days=20), TODAY + timedelta(days=25)
FEST_DAY = TODAY + timedelta(days=23)  # inside the multi-day festival
EMPTY_DAY = TODAY + timedelta(days=10)


@pytest.fixture(scope="module", autouse=True)
def city_events_data():
    rows = [
        EventFestival(id="cx-fest", destination_id="dest-testville", title="Lantern Festival", summary="Paper lanterns along the river.", start_date=FEST_START.isoformat(), end_date=FEST_END.isoformat(), event_type="festival", category="festival", significance="Marks the end of the harvest.", etiquette="Do not release lanterns near the fort.", expected_footfall=150000, is_ticketed=False, venue_name="Riverside", venue_lat=10.2, venue_lon=20.2, source="Testville Tourism Office", source_url="https://tourism.example/lanterns", status="active", last_verified_at="2026-09-01T00:00:00Z"),
        EventFestival(id="cx-cancelled", destination_id="dest-testville", title="Cancelled Parade", start_date=FEST_DAY.isoformat(), end_date=FEST_DAY.isoformat(), status="cancelled", source="Testville Tourism Office"),
        EventFestival(id="cx-other-city", destination_id="dest-elsewhere", title="Elsewhere Fair", start_date=FEST_DAY.isoformat(), end_date=FEST_DAY.isoformat(), venue_lat=10.001, venue_lon=20.001, source="x"),
    ]
    with SessionLocal() as db:
        for row in rows:
            db.merge(row)
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(delete(EventFestival).where(EventFestival.id.in_([r.id for r in rows]) | EventFestival.id.like("live-%")))
        db.commit()


def _testville():
    city, _ = resolve_city(destination_id="dest-testville")
    return city


# ---- dates ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text, start, end", [
    ("what's happening this weekend", date(2026, 9, 26), date(2026, 9, 27)),
    ("anything next week?", date(2026, 9, 28), date(2026, 10, 4)),
    ("events on Oct 22", date(2026, 10, 22), date(2026, 10, 22)),
    ("festivals on 22 October 2026", date(2026, 10, 22), date(2026, 10, 22)),
    ("festivals in October", date(2026, 10, 1), date(2026, 10, 31)),
    ("what's on tomorrow", date(2026, 9, 25), date(2026, 9, 25)),
    ("what's happening here", date(2026, 9, 24), date(2026, 9, 24)),
    ("events on Jan 3", date(2027, 1, 3), date(2027, 1, 3)),
])
def test_date_ranges_resolve_against_the_selected_date(text, start, end):
    window = parse_range(text, date(2026, 9, 24))  # a Thursday
    assert (window.start, window.end) == (start, end)


def test_weekend_of_a_weekend_day_is_that_weekend():
    assert (weekend_of(date(2026, 9, 26)).start, weekend_of(date(2026, 9, 26)).end) == (date(2026, 9, 26), date(2026, 9, 27))
    assert (weekend_of(date(2026, 9, 27)).start, weekend_of(date(2026, 9, 27)).end) == (date(2026, 9, 27), date(2026, 9, 27))


def test_listing_dates_are_read_or_dropped():
    anchor = date(2026, 9, 24)
    assert listing_dates("Oct 22", "Thu, Oct 22, 7 – 10 PM", anchor) == (date(2026, 10, 22), date(2026, 10, 22))
    assert listing_dates("Oct 20", "Oct 20 – 25", anchor) == (date(2026, 10, 20), date(2026, 10, 25))
    assert listing_dates(None, "Sat, Oct 24 – Sun, Oct 25", anchor) == (date(2026, 10, 24), date(2026, 10, 25))
    assert listing_dates(None, "Dates to be announced", anchor) is None


@pytest.mark.parametrize("title, expected", [
    ("Deepavali at the Fort", ("festival", "festival")), ("Monsoon Music Nights", ("live", "music")),
    ("City Marathon", ("live", "sports")), ("Craft Mela", ("live", "arts")), ("Street Food Week", ("live", "food")),
])
def test_event_classification(title, expected):
    assert classify(title) == expected


# ---- city resolution -----------------------------------------------------------------------

def test_gps_resolves_to_the_city_and_a_chosen_city_wins():
    by_gps, _ = resolve_city(lat=10.01, lon=20.01)
    assert by_gps.destination_id == "dest-testville" and by_gps.resolved_by == "coverage"
    chosen, _ = resolve_city(destination_id="dest-testville", lat=-40.0, lon=-40.0)
    assert chosen.name == "Testville"


def test_unknown_place_without_geocoder_is_unresolved_not_guessed():
    city, errors = resolve_city(lat=-40.0, lon=-40.0)
    assert city is None and errors and errors[0]["source"] == "nominatim"


# ---- events: city + date overlap, zero stays zero -----------------------------------------

def test_multi_day_event_matches_any_day_inside_it_and_is_city_scoped():
    result = city_events(_testville(), single(FEST_DAY), today=TODAY, live=False)
    names = [e["name"] for e in result["events"]]
    assert names == ["Lantern Festival"]  # cancelled excluded; the other city's event excluded despite a nearby venue
    event = result["events"][0]
    assert event["on_selected_date"] and event["multi_day"] and event["source"]["url"] == "https://tourism.example/lanterns"
    assert event["crowded"] is True and result["groups"][0]["label"] == "Festivals"


def test_no_events_is_explicit_and_nothing_is_substituted():
    result = city_events(_testville(), single(EMPTY_DAY), today=TODAY, live=False)
    assert result["events"] == [] and result["event_status"] == NONE
    assert result["message"].startswith("No verified events found for Testville on")


def test_associated_festival_is_never_shown_as_happening():
    with SessionLocal() as db:
        db.merge(EventFestival(id="cx-assoc", destination_id="dest-testville", title="Old Harvest Utsav", typical_months=f"[{EMPTY_DAY.month}]", source="curated"))
        db.commit()
    try:
        result = city_events(_testville(), single(EMPTY_DAY), today=TODAY, live=False)
        assert result["events"] == [] and "Old Harvest Utsav" in [f["name"] for f in result["associated_festivals"]]
    finally:
        with SessionLocal() as db:
            db.execute(delete(EventFestival).where(EventFestival.id == "cx-assoc"))
            db.commit()


class FakeEvents(SerpApiClient):
    def __init__(self, results):
        super().__init__(api_key="test")
        self.results, self.calls = results, []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SearchResponse(query, kwargs.get("engine"), list(self.results), "2026-09-24T00:00:00Z")


def _listing(title, start, when=None, venue=None):
    return SearchResult(title=title, url="https://listings.example/" + title.replace(" ", "-"), snippet=f"{title} details", source="listings.example", engine="google_events", event_start=start, event_date=when or start, venue_name=venue)


def test_live_listings_keep_only_dated_matches_and_store_evidence():
    day = TODAY + timedelta(days=3)
    label = day.strftime("%b ") + str(day.day)
    fake = FakeEvents([
        _listing("Jazz on the Ghats", label, f"{label}, 7 – 10 PM", "Ghat Stage"),
        _listing("Far Future Expo", (day + timedelta(days=40)).strftime("%b ") + str((day + timedelta(days=40)).day)),
        _listing("Mystery Gig", None, "Dates to be announced"),
    ])
    statuses = refresh_live_events(_testville(), single(day), TODAY, web=fake)
    serp = next(s for s in statuses if s["source"].startswith("Google Events"))
    assert (serp["found"], serp["kept"], serp["undated"]) == (3, 1, 1)
    result = city_events(_testville(), single(day), today=TODAY, live=False)
    jazz = next(e for e in result["events"] if e["name"] == "Jazz on the Ghats")
    assert jazz["category"] == "music" and jazz["venue"]["name"] == "Ghat Stage"
    assert jazz["source"]["kind"] == "live" and jazz["source"]["last_verified_at"] and jazz["source"]["url"].startswith("https://listings.example/")


def test_live_listing_duplicating_a_stored_record_is_not_added_again():
    label = FEST_DAY.strftime("%b ") + str(FEST_DAY.day)
    refresh_live_events(_testville(), single(FEST_DAY), TODAY, web=FakeEvents([_listing("Lantern Festival", label)]))
    names = [e["name"] for e in city_events(_testville(), single(FEST_DAY), today=TODAY, live=False)["events"]]
    assert names.count("Lantern Festival") == 1


def test_past_dates_skip_live_listings_and_unconfigured_search_is_reported():
    past = refresh_live_events(_testville(), single(TODAY - timedelta(days=30)), TODAY, web=FakeEvents([]))
    assert past[0]["status"] == "not_applicable"
    statuses = refresh_live_events(_testville(), single(TODAY + timedelta(days=5)), TODAY, web=SerpApiClient(api_key=""))
    assert {s["source"]: s["status"] for s in statuses}["Google Events (SerpApi)"] == "not_configured"


# ---- weather for a date ----------------------------------------------------------------------

def _daily(day):
    return {"timezone": "UTC", "daily": {"time": [day], "weather_code": [63], "temperature_2m_max": [24.0], "temperature_2m_min": [16.0], "apparent_temperature_max": [25.0], "precipitation_sum": [9.0], "sunrise": [f"{day}T06:00"], "sunset": [f"{day}T18:00"]}}


def test_weather_basis_follows_the_date(weather_ok, monkeypatch):
    weather_ok()
    assert weather_on(10.0, 20.0, TODAY, timezone_name="UTC")["basis"] == "forecast"
    monkeypatch.setattr("app.weather.open_meteo.get_json", lambda provider, url, params=None, **kw: _daily(params["start_date"]))
    past = weather_on(10.0, 20.0, TODAY - timedelta(days=200), timezone_name="UTC")
    assert past["basis"] == "observed" and past["day"]["date"] == (TODAY - timedelta(days=200)).isoformat()
    typical = weather_on(10.0, 20.0, TODAY + timedelta(days=60), timezone_name="UTC")
    assert typical["basis"] == "typical" and "not a forecast" in typical["basis_label"] and len(typical["day"]["years"]) == 3
    assert "rain" in typical["signals"]


def test_dataset_record_is_used_beyond_the_forecast_and_offline_is_unavailable():
    far = TODAY + timedelta(days=40)
    assert weather_on(10.0, 20.0, far, timezone_name="UTC", destination_id="dest-testville")["status"] == "unavailable"  # offline, no record
    with SessionLocal() as db:
        db.merge(WeatherDaily(id="cx-w", destination_id="dest-testville", for_date=far.isoformat(), temp_min_c=20, temp_max_c=30, feels_like_c=33, precipitation_mm=1, condition="clear"))
        db.commit()
    try:
        weather = weather_on(10.0, 20.0, far, timezone_name="UTC", destination_id="dest-testville")
        assert weather["basis"] == "dataset" and weather["live"] is False
    finally:
        with SessionLocal() as db:
            db.execute(delete(WeatherDaily).where(WeatherDaily.id == "cx-w"))
            db.commit()


# ---- the three judge states, end to end ------------------------------------------------------

def test_today_festival_date_and_empty_date(weather_ok, monkeypatch):
    weather_ok(precipitation=70, code=63)
    with TestClient(app) as client:
        today = client.get("/api/context", params={"destination_id": "dest-testville"}).json()
        assert today["date"]["kind"] == "today" and today["weather"]["basis"] == "forecast"
        assert any(t["kind"] == "weather" and "umbrella" in t["text"] for t in today["tips"])

        festival = client.get("/api/context", params={"destination_id": "dest-testville", "date": FEST_DAY.isoformat()}).json()
        assert [e["name"] for e in festival["events"]["events"]] == ["Lantern Festival"]
        assert any("Lantern Festival is on" in t["text"] for t in festival["tips"])
        assert any(t["kind"] == "etiquette" and "lanterns" in t["text"] for t in festival["tips"])
        assert festival["date"]["kind"] == "future" and festival["season"]["name"]

        empty = client.get("/api/context", params={"destination_id": "dest-testville", "date": EMPTY_DAY.isoformat()}).json()
        assert empty["events"]["events"] == [] and empty["events"]["event_status"] == NONE
        assert "No verified events found for Testville" in empty["briefing"]["text"]
        assert "Lantern" not in empty["briefing"]["text"]


def test_events_endpoint_and_ask_use_the_selected_date(gps):
    with TestClient(app) as client:
        week = client.get("/api/events", params={"destination_id": "dest-testville", "date": (FEST_START - timedelta(days=2)).isoformat(), "when": "next 7 days"}).json()
        assert [e["name"] for e in week["events"]] == ["Lantern Festival"] and week["range"]["kind"] == "week"
        answer = client.post("/api/ask", json={"question": "What's happening here?", "user_location": gps(10.01, 20.01), "date": FEST_DAY.isoformat()}).json()
        assert answer["event_listing"]["event_status"] == "verified_events_found" and answer["events"][0]["name"] == "Lantern Festival"
        nothing = client.post("/api/ask", json={"question": "What's happening here?", "user_location": gps(10.01, 20.01), "date": EMPTY_DAY.isoformat()}).json()
        assert nothing["events"] == [] and "No verified events found for Testville" in nothing["answer"]
        bad = client.get("/api/context", params={"destination_id": "dest-testville", "date": "22/10/2026"})
        assert bad.status_code == 422


# ---- the model may not invent events ---------------------------------------------------------

def test_invented_event_is_rejected_and_the_answer_falls_back():
    evidence = EvidenceBuilder()
    evidence.add_event_status("Testville", "Sat 10 Oct 2026", 0, ["stored events"])
    evidence.add("knowledge", "History of Testville", "Testville was founded as a river trading post in 1450.")
    invented = "Testville was a trading post [E2]. Don't miss the Testville Music Festival this weekend [E1]."
    assert any(i["type"] == "invented_event" for i in validate(invented, evidence.items).issues)
    llm = ScriptedLLM([invented, "Also try Diwali celebrations in the old town [E1]."])
    context = AnswerContext(question="Brief me", intent="BRIEFING", location_notes=[], evidence=evidence.items, extra={"no_events_message": "No verified events found for Testville on Sat 10 Oct 2026 in the available sources."})
    answer = generate(context, client=llm)
    assert answer.mode == "deterministic" and "Music Festival" not in answer.text and "Diwali" not in answer.text
    assert "No verified events found for Testville" in answer.text
    assert "do not infer, invent, suggest or substitute" in llm.prompts[0][0]["content"]
