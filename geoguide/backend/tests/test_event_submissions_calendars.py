"""Official festival calendars and reviewed event submissions (India coverage without partner APIs)."""
import json
from pathlib import Path
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.dates import single
from app.events.engine import build_query, find_events
from app.events.providers.calendar import CalendarProvider, load_calendars
from app.events.providers.stored import StoredEventProvider
from app.events.submissions import SubmissionError, parse_submission
from app.geo.city import City, resolve_city
from app.main import app

TODAY = datetime.now(timezone.utc).date()
REAL_CALENDARS = Path(__file__).resolve().parent.parent / "data" / "sources" / "festival_calendars"


# ---- official calendars ------------------------------------------------------------------------

def _bengaluru(region="Karnataka"):
    return City(name="Bengaluru", country="India", region=region, lat=12.97, lon=77.59, timezone="Asia/Kolkata", country_code="IN")


def _calendar_events(city, day, directory=REAL_CALENDARS):
    load_calendars.cache_clear()
    return find_events(build_query(city, single(day)), providers=[CalendarProvider(directory)])


def test_official_calendar_shows_the_gazetted_date_in_indian_cities():
    result = _calendar_events(_bengaluru(), date(2026, 11, 8))
    diwali = [e for e in result["events"] if e.title == "Diwali (Deepavali)"]
    assert len(diwali) == 1
    event = diwali[0]
    assert event.source.kind == "official_calendar" and "Personnel" in event.source.name and event.source.url.startswith("https://") and event.event_url is None  # one link per calendar, not per day
    assert event.type == "festival" and event.time_known is False and event.price_kind == "unknown"


def test_state_days_only_apply_in_that_state_and_fixed_dates_repeat_every_year():
    in_karnataka = [e.title for e in _calendar_events(_bengaluru(), date(2027, 11, 1))["events"]]
    elsewhere = [e.title for e in _calendar_events(_bengaluru(region="Maharashtra"), date(2027, 11, 1))["events"]]
    assert "Karnataka Rajyotsava" in in_karnataka and "Karnataka Rajyotsava" not in elsewhere


def _write(folder, name, entries, **extra):
    calendar = {"id": name, "title": name, "authority": "Test Office", "source_url": "https://gov.example/c", "country": "IN", "regions": [], "note": "Holiday.", "entries": entries, **extra}
    (folder / f"{name}.json").write_text(json.dumps(calendar))


def test_moon_dependent_dates_say_so_and_duplicates_across_calendars_merge(tmp_path):
    day = TODAY + timedelta(days=3)
    _write(tmp_path, "a", [{"title": "Id-ul-Fitr", "date": day.isoformat(), "category": "religious", "kind": "festival", "moon_dependent": True},
                           {"title": "Founders Day", "annual": day.strftime("%m-%d"), "category": "cultural", "kind": "national_day"}])
    _write(tmp_path, "b", [{"title": "Founders Day", "annual": day.strftime("%m-%d"), "category": "cultural", "kind": "national_day"}])
    events = _calendar_events(_bengaluru(), day, tmp_path)["events"]
    assert "moon" in next(e for e in events if e.title == "Id-ul-Fitr").description
    assert [e.title for e in events].count("Founders Day") == 1


def test_calendars_never_apply_to_other_countries():
    zurich = City(name="Zurich", country="Switzerland", region=None, lat=47.37, lon=8.54, timezone="Europe/Zurich", country_code="CH")
    assert _calendar_events(zurich, date(2026, 11, 8))["events"] == []


def test_every_calendar_entry_has_a_real_date_and_a_source():
    load_calendars.cache_clear()
    for calendar in load_calendars(REAL_CALENDARS):
        assert calendar["source_url"].startswith("https://") and calendar["authority"] and calendar["verification"] and calendar["verified_on"]
        for entry in calendar["entries"]:
            assert bool(entry.get("date")) != bool(entry.get("annual"))
            if entry.get("date"):
                assert date.fromisoformat(entry["date"]).year == calendar["year"]


def test_calendar_days_are_not_shown_once_they_are_over(tmp_path):
    past = TODAY - timedelta(days=2)
    _write(tmp_path, "p", [{"title": "Founders Day", "date": past.isoformat(), "category": "cultural", "kind": "national_day"}])
    assert _calendar_events(_bengaluru(), past, tmp_path)["events"] == []


# ---- submissions: validation -------------------------------------------------------------------

def _payload(**changes):
    base = {"role": "organiser", "title": "Riverside Poetry Evening", "organiser_name": "Testville Poets", "contact_email": "poets@example.com", "category": "education",
            "start_date": (TODAY + timedelta(days=5)).isoformat(), "start_time": "18:00", "end_time": "20:00", "destination_id": "dest-testville",
            "venue_name": "Riverside Steps", "price_kind": "free", "description": "Open reading by the river."}
    base.update(changes)
    return base


@pytest.mark.parametrize("changes, field", [
    ({"role": "promoter"}, "role"),
    ({"title": "x"}, "title"),
    ({"contact_email": "not-an-email"}, "contact_email"),
    ({"category": "raves"}, "category"),
    ({"start_date": ""}, "start_date"),
    ({"start_date": "2026-13-40"}, "start_date"),
    ({"end_date": (TODAY + timedelta(days=1)).isoformat()}, "end_date"),
    ({"start_date": (TODAY - timedelta(days=10)).isoformat()}, "end_date"),
    ({"start_date": (TODAY + timedelta(days=1)).isoformat(), "end_date": (TODAY + timedelta(days=90)).isoformat()}, "end_date"),
    ({"start_time": "25:00"}, "start_time"),
    ({"start_time": "20:00", "end_time": "19:00"}, "end_time"),
    ({"destination_id": "dest-nowhere"}, "destination_id"),
    ({"venue_lat": 40.0, "venue_lon": -70.0}, "venue_lat"),
    ({"price_kind": "paid", "price_min": "0"}, "price_min"),
    ({"price_kind": "paid", "price_min": "abc"}, "price_min"),
    ({"ticket_url": "javascript:alert(1)"}, "ticket_url"),
])
def test_invalid_submissions_name_the_field(changes, field):
    with pytest.raises(SubmissionError) as error:
        parse_submission(_payload(**changes), today=TODAY)
    assert error.value.field == field


def test_valid_paid_submission_normalises_price_and_currency():
    parsed = parse_submission(_payload(price_kind="paid", price_min="1,250", currency="inr", end_date=(TODAY + timedelta(days=6)).isoformat()), today=TODAY)
    assert parsed.values["price_min"] == "1250.00" and parsed.values["currency"] == "INR" and parsed.values["end_date"] > parsed.values["start_date"]


# ---- submissions: review flow over the API -----------------------------------------------------

def _token(client, email):
    return client.post("/api/auth/signup", json={"name": email.split("@")[0], "email": email, "password": "secret123"}).json()["token"]


def test_submission_is_hidden_until_approved_then_published_with_its_source(monkeypatch):
    monkeypatch.setattr("app.events.submissions.EVENT_MODERATOR_EMAILS", {"mod@example.com"})
    client = TestClient(app)
    organiser = {"Authorization": f"Bearer {_token(client, 'organiser@example.com')}"}
    moderator = {"Authorization": f"Bearer {_token(client, 'mod@example.com')}"}
    day = TODAY + timedelta(days=7)

    assert client.post("/api/events/submissions", json=_payload(start_date=day.isoformat())).status_code == 401
    created = client.post("/api/events/submissions", json=_payload(start_date=day.isoformat()), headers=organiser)
    assert created.status_code == 200 and created.json()["submission"]["status"] == "pending"
    submission_id = created.json()["submission"]["id"]

    city = resolve_city(destination_id="dest-testville")[0]
    stored = lambda: [e.title for e in find_events(build_query(city, single(day)), providers=[StoredEventProvider()])["events"]]  # noqa: E731
    assert "Riverside Poetry Evening" not in stored()  # nothing is shown before review

    duplicate = client.post("/api/events/submissions", json=_payload(start_date=day.isoformat(), title="Riverside Poetry Evening!"), headers=organiser)
    assert duplicate.status_code == 409

    assert client.get("/api/events/submissions/review", headers=organiser).status_code == 403
    queue = client.get("/api/events/submissions/review", headers=moderator).json()["items"]
    assert [item["id"] for item in queue] == [submission_id] and queue[0]["contact_email"] == "poets@example.com"
    assert client.post(f"/api/events/submissions/{submission_id}/review", json={"decision": "reject"}, headers=moderator).status_code == 422  # a reason is required

    approved = client.post(f"/api/events/submissions/{submission_id}/review", json={"decision": "approve"}, headers=moderator).json()["submission"]
    assert approved["status"] == "approved" and approved["event_id"]
    assert client.post(f"/api/events/submissions/{submission_id}/review", json={"decision": "approve"}, headers=moderator).status_code == 409

    events = find_events(build_query(city, single(day)), providers=[StoredEventProvider()])["events"]
    event = next(e for e in events if e.title == "Riverside Poetry Evening")
    assert event.source.kind == "stored_submission" and event.source.name == "Testville Poets (organiser submission, reviewed)"
    assert event.time_known and event.start.time() == time(18, 0) and event.price_kind == "free"
    public = event.as_dict() if hasattr(event, "as_dict") else {}
    assert "poets@example.com" not in json.dumps(public, default=str)  # the contact email is never published

    mine = client.get("/api/events/submissions/mine", headers=organiser).json()
    assert mine["moderator"] is False and mine["items"][0]["status"] == "approved"


def test_rejection_reason_reaches_the_submitter(monkeypatch):
    monkeypatch.setattr("app.events.submissions.EVENT_MODERATOR_EMAILS", {"mod2@example.com"})
    client = TestClient(app)
    organiser = {"Authorization": f"Bearer {_token(client, 'venue@example.com')}"}
    moderator = {"Authorization": f"Bearer {_token(client, 'mod2@example.com')}"}
    created = client.post("/api/events/submissions", json=_payload(title="Lantern Making Workshop", role="venue", category="workshops"), headers=organiser).json()["submission"]
    client.post(f"/api/events/submissions/{created['id']}/review", json={"decision": "reject", "note": "Could not confirm the date with the venue."}, headers=moderator)
    mine = client.get("/api/events/submissions/mine", headers=organiser).json()["items"]
    assert mine[0]["status"] == "rejected" and mine[0]["review_note"] == "Could not confirm the date with the venue."
