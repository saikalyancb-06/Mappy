from fastapi.testclient import TestClient

from app.main import app


def test_location_endpoint_handles_missing_payload_without_crashing():
    client = TestClient(app)
    response = client.post('/api/location', json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'queued'
    assert 'job_id' in payload


def test_location_endpoint_handles_invalid_coordinates_without_crashing():
    client = TestClient(app)
    response = client.post('/api/location', json={'lat': 'bad', 'lon': 'oops'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'queued'
    assert payload['area_id']
