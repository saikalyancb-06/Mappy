from fastapi.testclient import TestClient

from app.main import app


def test_location_starts_ingestion_job():
    client = TestClient(app)
    response = client.post('/api/location', json={'lat': 12.9716, 'lon': 77.5946, 'accuracy': 12})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'queued'
    assert 'job_id' in payload
    assert payload['progress'] == 10


def test_ingestion_events_endpoint_returns_status():
    client = TestClient(app)
    start = client.post('/api/location', json={'lat': 12.9716, 'lon': 77.5946, 'accuracy': 12}).json()
    job_id = start['job_id']
    response = client.get(f'/api/ingestion/{job_id}/events')
    assert response.status_code == 200
    payload = response.json()
    assert payload['job_id'] == job_id
    assert payload['status'] == 'running'
