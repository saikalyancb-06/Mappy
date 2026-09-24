"""End-to-end API behaviour with providers offline or faked."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import HAMPI_PACK

TESTVILLE = {"id": "dest-testville"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_capabilities_without_secrets(client):
    body = client.get("/api/health").json()
    services = body["services"]
    assert services["database"]["dialect"] == "sqlite" and services["database"]["destinations"] >= 1
    assert services["web_search"]["configured"] is False
    assert "api_key" not in json.dumps(body).lower()


def test_client_config_comes_from_taxonomy(client):
    config = client.get("/api/config").json()
    assert {"id": "heritage", "label": "Heritage"} in config["interests"]
    assert "less_walking" in config["plan_presets"]


def test_ask_near_me_without_location_asks_for_it(client):
    body = client.post("/api/ask", json={"question": "coffee shops near me", "active_destination": TESTVILLE}).json()
    assert body["needs"] == "user_location" and body["results"] == []
    assert "location" in body["answer"].lower()


def test_ask_near_me_uses_gps_and_returns_structured_results(client, gps):
    body = client.post("/api/ask", json={"question": "cafes near me", "user_location": gps(10.0, 20.0)}).json()
    assert body["intent"]["intent"] == "NEARBY_SEARCH"
    assert body["geo_context"]["reference"]["origin"] == "user_location"
    names = [r["name"] for r in body["results"]]
    assert names and all(r["category"] == "cafe" for r in body["results"])
    assert "Sunrise Cafe" in names
    assert all(r["distance_km"] <= body["geo_context"]["reference"]["radius_km"] for r in body["results"])
    assert body["sources"] and body["sources"][0]["source_type"] == "poi"
    assert any("web search is not configured" in n.lower() for n in body["notices"])


def test_ask_explicit_destination_overrides_physical_location(client, gps):
    body = client.post("/api/ask", json={"question": "What should I visit in Testville?", "user_location": gps(-33.0, 151.0)}).json()
    reference = body["geo_context"]["reference"]
    assert reference["origin"] == "query_destination" and reference["destination_id"] == "dest-testville"
    assert body["next_active_destination"]["destination_id"] == "dest-testville"
    assert body["results"] and all(r["kind"] in {"attraction", "activity"} for r in body["results"])
    assert "tv-far-lake" not in [r["id"] for r in body["results"]]  # outside coverage radius


def test_ask_knowledge_is_grounded_with_citations(client):
    body = client.post("/api/ask", json={"question": "What is the history of Testville?"}).json()
    assert body["intent"]["intent"] == "DESTINATION_KNOWLEDGE"
    assert "1450" in body["answer"] and "[E1]" in body["answer"]
    assert body["answer_meta"]["mode"] == "deterministic"  # no LLM key in tests


def test_ask_place_lookup_resolves_entity_and_facts(client, gps):
    body = client.post("/api/ask", json={"question": "How far is the Old Fort?", "user_location": gps(10.0, 20.009)}).json()
    assert body["entity_resolution"]["status"] == "resolved" and body["entity_resolution"]["entity_id"] == "tv-old-fort"
    assert any(s["source_type"] == "knowledge" for s in body["sources"])
    assert "from the traveller" in body["sources"][0]["content"]


def test_ask_unknown_business_is_not_hallucinated(client):
    body = client.post("/api/ask", json={"question": "Where is Quantum Noodle Palace in Market Street?", "active_destination": TESTVILLE}).json()
    assert body["entity_resolution"]["status"] == "not_found"
    assert body["results"] == [] and "couldn't verify" in body["answer"]


def test_ask_weather_unavailable_does_not_invent(client):
    body = client.post("/api/ask", json={"question": "What is the weather there?", "active_destination": TESTVILLE}).json()
    assert body["intent"]["intent"] == "WEATHER"
    assert "unavailable" in body["answer"].lower() and body["weather"]["status"] == "unavailable"


def test_ask_weather_uses_forecast_day(client, weather_ok):
    weather_ok(apparent_max=38.0)
    body = client.post("/api/ask", json={"question": "Weather in Testville tomorrow"}).json()
    assert body["weather"]["day_offset"] == 1 and "heat" in body["weather"]["signals"]
    assert body["sources"][0]["source_type"] == "weather"


def test_ask_safety_preserves_severity(client):
    body = client.post("/api/ask", json={"question": "Is Testville safe?"}).json()
    severities = {item["id"]: item["severity"] for item in body["safety"]}
    assert severities.get("adv-tv-river") == "high"


def test_ask_itinerary_returns_feasible_plan(client, weather_ok):
    weather_ok()
    body = client.post("/api/ask", json={"question": "plan 3 hours in Testville"}).json()
    plan = body["plan"]
    assert body["intent"]["intent"] == "ITINERARY"
    assert plan["stops"] and plan["used_min"] <= 180


def test_ask_debug_trace_explains_retrieval(client, gps):
    body = client.post("/api/ask", json={"question": "temples near me", "user_location": gps(10.0, 20.0), "debug": True}).json()
    steps = [step["step"] for step in body["debug"]["steps"]]
    assert "discovery" in steps
    discovery = next(step for step in body["debug"]["steps"] if step["step"] == "discovery")
    assert discovery["ranking"] and "scores" in discovery["ranking"][0]


def test_nearby_endpoint_and_origin_rules(client, gps):
    by_destination = client.get("/api/nearby", params={"destination_id": "dest-testville"}).json()
    assert by_destination["geo_context"]["reference"]["origin"] == "active_destination"
    assert by_destination["items"]
    missing = client.get("/api/nearby", params={"origin": "user", "destination_id": "dest-testville"})
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "user_location_required"
    assert client.get("/api/nearby").status_code == 422  # no location, no destination: no default city
    stays = client.get("/api/nearby", params={"destination_id": "dest-testville", "category": "hotel"}).json()
    assert [i["id"] for i in stays["items"]] == ["tv-grand-hotel"]
    assert client.get("/api/nearby", params={"destination_id": "dest-testville", "category": "spaceport"}).status_code == 422


def test_now_endpoint_builds_grounded_briefing(client, weather_ok):
    weather_ok()
    body = client.get("/api/now", params={"destination_id": "dest-testville"}).json()
    assert body["place"]["name"] == "Testville" and body["suggestions"]
    assert body["briefing"]["text"] and body["briefing"]["sources"]
    assert body["pack"]["provenance"]["dataset"] == "Testville synthetic pack"


def test_plan_endpoint_and_replan(client, weather_ok):
    weather_ok()
    first = client.post("/api/plan", json={"active_destination": TESTVILLE, "duration": "full", "start": "09:00", "day_offset": 1}).json()["plan"]
    second = client.post("/api/plan", json={"active_destination": TESTVILLE, "duration": "full", "start": "09:00", "day_offset": 1, "preset": "less_walking", "previous": first}).json()["plan"]
    assert first["stops"] and second["totals"]["walking_km"] <= first["totals"]["walking_km"]
    assert client.post("/api/plan", json={"active_destination": TESTVILLE, "duration": "3 days"}).status_code == 422


def test_place_detail_and_destinations(client):
    detail = client.get("/api/places/tv-old-fort").json()
    assert detail["place"]["name"] == "Old Fort" and detail["facts"]
    assert client.get("/api/places/nope").status_code == 404
    listing = client.get("/api/destinations", params={"q": "testvile"}).json()
    assert listing["items"][0]["id"] == "dest-testville"
    assert client.get("/api/destinations/resolve", params={"q": "Old Testville"}).json()["place"]["destination_id"] == "dest-testville"
    assert client.get("/api/destinations/resolve", params={"q": "Zzqx Unknown"}).status_code == 404


def test_auth_preferences_personalise_ranking(client, gps):
    signup = client.post("/api/auth/signup", json={"email": "t@example.com", "password": "secret1", "name": "T"}).json()
    headers = {"Authorization": f"Bearer {signup['token']}"}
    saved = client.put("/api/preferences", json={"interests": {"culture": 1}, "budget": "low", "accessibility": ["step_free"], "language": "kn"}, headers=headers).json()
    assert saved["accessibility"] == ["step_free"] and saved["language"] == "kn"
    body = client.get("/api/nearby", params={"destination_id": "dest-testville"}, headers=headers).json()
    assert "tv-old-fort" not in [i["id"] for i in body["items"]]  # not step-free
    assert client.get("/api/preferences").status_code == 401


def test_demo_pack_is_valid_data():
    """The demo destination is data: it must load with the generic loader and be internally consistent."""
    pois = json.loads((HAMPI_PACK / "pois.json").read_text())
    ids = {p["id"] for p in pois}
    facts = json.loads((HAMPI_PACK / "poi_facts.json").read_text())
    assert all(f["poi_id"] in ids for f in facts)
    from app.core.rules import taxonomy

    assert all(p["category"] in taxonomy()["categories"] for p in pois)
    assert all(-90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180 for p in pois)
