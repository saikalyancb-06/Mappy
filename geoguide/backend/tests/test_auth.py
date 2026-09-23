from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def test_signup_login_and_logout_flow():
    client = TestClient(app)
    email = f'{uuid4().hex}@example.com'
    signup = client.post('/api/auth/signup', json={'name': 'Test traveler', 'email': email, 'password': 'strong-pass'})
    assert signup.status_code == 200
    token = signup.json()['token']
    assert token

    login = client.post('/api/auth/login', json={'email': email, 'password': 'strong-pass'})
    assert login.status_code == 200
    client.headers.update({'Authorization': f"Bearer {login.json()['token']}"})
    assert client.get('/api/auth/me').json()['user']['email'] == email
    assert client.post('/api/auth/logout').json()['status'] == 'ok'


def test_login_rejects_wrong_password():
    client = TestClient(app)
    email = f'{uuid4().hex}@example.com'
    client.post('/api/auth/signup', json={'email': email, 'password': 'strong-pass'})
    response = client.post('/api/auth/login', json={'email': email, 'password': 'wrong-pass'})
    assert response.status_code == 401