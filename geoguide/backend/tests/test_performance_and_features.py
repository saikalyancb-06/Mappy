"""Performance work (regex-free phrase matching, parallel fan-out, cached city index, deferred
briefing, compression) and the new features (similar places, calendar-ready plans)."""
import re
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.concurrency import map_parallel, run_parallel
from app.core.text import has_phrase, normalize
from app.db.models import Destination
from app.db.session import SessionLocal
from app.geo.geocoding import nearest_destination
from app.main import app
from app.services.similar import similar_places

TESTVILLE = {"id": "dest-testville"}


@pytest.fixture
def client():
    return TestClient(app)


# ---- phrase matching without regex compilation ------------------------------------------------------

@pytest.mark.parametrize("text, phrase", [
    ("find places near city center", "city center"), ("museums", "museums"), ("temples and museums", "and"),
    ("the museum", "museums"), ("walk", "walking"), ("sunset point view", "sunset point"), ("", "x"), ("abc", ""),
])
def test_phrase_test_matches_the_old_regex_exactly(text, phrase):
    norm, target = normalize(text), normalize(phrase)
    old = bool(target) and re.search(rf"(?:^|\s){re.escape(target)}(?:\s|$)", norm) is not None
    assert has_phrase(norm, phrase) == old


def test_normalize_is_cached_and_stable():
    assert normalize("Café  Ñandú & Co.") == normalize("Café  Ñandú & Co.") == "cafe nandu and co"
    assert normalize(None) == "" and normalize("") == ""


# ---- parallel fan-out ---------------------------------------------------------------------------------

def test_independent_steps_run_concurrently_and_keep_their_results():
    started = time.monotonic()
    results = run_parallel({name: (lambda n=name: (time.sleep(0.2), n)[1]) for name in ("a", "b", "c", "d")})
    assert results == {"a": "a", "b": "b", "c": "c", "d": "d"} and time.monotonic() - started < 0.6
    assert map_parallel(lambda x: x * 2, [3, 1, 2]) == [6, 2, 4]  # order preserved


def test_nested_fan_out_cannot_deadlock():
    def inner(i):
        return sum(map_parallel(lambda x: x, list(range(5)))) + i

    assert run_parallel({str(i): (lambda i=i: sum(map_parallel(inner, list(range(8)))) + i) for i in range(40)})["0"] == sum(10 + j for j in range(8))


def test_errors_in_parallel_steps_surface():
    with pytest.raises(ValueError):
        run_parallel({"ok": lambda: 1, "bad": lambda: (_ for _ in ()).throw(ValueError("boom"))})


# ---- cached city index ---------------------------------------------------------------------------------

def test_city_index_sees_new_cities_immediately():
    point = (-45.5, 170.5)
    assert nearest_destination(*point) is None  # warm the index
    city_id = f"dest-idx-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(Destination(id=city_id, name="Indexville", lat=point[0], lon=point[1], coverage_radius_km=5))
        db.commit()
    try:
        assert nearest_destination(*point).id == city_id
    finally:
        with SessionLocal() as db:
            db.execute(delete(Destination).where(Destination.id == city_id))  # bulk delete bypasses ORM events
            db.commit()
    assert nearest_destination(*point) is None  # stale index entry detected and rebuilt


def test_city_index_is_thread_safe():
    errors = []

    def look():
        try:
            for _ in range(50):
                nearest_destination(10.0, 20.0)
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=look) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


# ---- deferred briefing + compression ----------------------------------------------------------------

def test_context_can_be_served_before_the_ai_briefing(client, weather_ok):
    weather_ok()
    fast = client.get("/api/context", params={"destination_id": "dest-testville", "briefing": "deferred"}).json()
    assert fast["briefing"]["pending"] is True and fast["briefing"]["mode"] == "deterministic" and fast["briefing"]["text"]
    later = client.get("/api/context/briefing", params={"destination_id": "dest-testville"}).json()
    assert later["briefing"].get("pending") is None and later["briefing"]["text"] and later["date"]["selected"] == fast["date"]["selected"]
    assert client.get("/api/context", params={"destination_id": "dest-testville", "briefing": "later"}).status_code == 422


def test_large_responses_are_compressed(client, weather_ok):
    weather_ok()
    response = client.get("/api/context", params={"destination_id": "dest-testville", "briefing": "deferred"}, headers={"Accept-Encoding": "gzip"})
    assert response.headers.get("content-encoding") == "gzip"


# ---- similar places ------------------------------------------------------------------------------------

def test_similar_places_share_category_or_kind_and_explain_why():
    items = similar_places("tv-old-fort", limit=5)
    ids = [item["id"] for item in items]
    assert ids and "tv-old-fort" not in ids
    assert all(item["kind"] == "attraction" for item in items)
    assert all(item["similar_because"] or item["similarity"] >= 0.35 for item in items)
    assert similar_places("does-not-exist") == []


def test_similar_places_respect_the_recommendation_policy(client):
    everyone = [item["id"] for item in similar_places("tv-old-fort", limit=10)]
    opted_out = [item["id"] for item in similar_places("tv-old-fort", profile={"exclude_places_of_worship": True}, limit=10)]
    assert "tv-lotus-temple" not in opted_out
    assert set(opted_out) <= set(everyone)
    assert client.get("/api/places/tv-old-fort/similar").status_code == 200


# ---- plans are ready for calendar export -------------------------------------------------------------

def test_plan_carries_timezone_and_stop_addresses_for_export(client, weather_ok):
    weather_ok()
    plan = client.post("/api/plan", json={"active_destination": TESTVILLE, "duration": "full", "start": "09:00", "day_offset": 1}).json()["plan"]
    assert plan["timezone"] == "UTC" and len(plan["start"]) == 16
    assert plan["stops"] and all("address" in stop and stop["lat"] is not None for stop in plan["stops"])
