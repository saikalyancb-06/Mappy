"""Offline city pack: what the app stores on the device for use without a connection."""
import json

from fastapi.testclient import TestClient

from app.cities.offline import build_offline_pack
from app.main import app


def test_pack_contains_places_with_hours_knowledge_events_and_taxonomy():
    pack = build_offline_pack("dest-testville")
    names = {p["name"] for p in pack["places"]}
    assert {"Old Fort", "Testville Museum"} <= names
    fort = next(p for p in pack["places"] if p["name"] == "Old Fort")
    assert fort["opening_hours"]["weekly"]["daily"] == [["08:00", "18:00"]]  # "open now" works offline
    assert pack["knowledge"] and all(k["content"] and "source" in k for k in pack["knowledge"])
    assert "museum" in pack["taxonomy"]["categories"] and "heritage" in pack["taxonomy"]["groups"]
    assert pack["city"]["id"] == "dest-testville" and pack["format"] == 1 and pack["version"]
    assert "events" in pack and pack["events_until"]


def test_pack_leaves_out_bad_and_excluded_places_and_follows_the_traveller_setting():
    everyone = {p["id"] for p in build_offline_pack("dest-testville")["places"]}
    opted_out = {p["id"] for p in build_offline_pack("dest-testville", {"exclude_places_of_worship": True})["places"]}
    assert "tv-lotus-temple" in everyone and "tv-lotus-temple" not in opted_out
    assert "tv-bad-coords" not in everyone


def test_pack_is_compact_and_served_compressed():
    client = TestClient(app)
    response = client.get("/api/destinations/dest-testville/offline-pack", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200 and response.headers.get("content-encoding") == "gzip"
    assert len(json.dumps(response.json())) < 200_000
    assert client.get("/api/destinations/nope/offline-pack").status_code == 404
