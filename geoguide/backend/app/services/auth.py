from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from app.config import APP_ENV, AUTH_SECRET

_TOKEN_SECRET = AUTH_SECRET.encode()
_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 210_000)
    return f'pbkdf2_sha256$210000${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def issue_token(user_id: str) -> str:
    payload = {'sub': user_id, 'exp': int(time.time()) + _TOKEN_TTL_SECONDS, 'jti': str(uuid.uuid4())}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    signature = hmac.new(_TOKEN_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
    return f'{encoded}.{signature}'


def verify_token(token: str | None) -> str | None:
    try:
        encoded, signature = (token or '').split('.', 1)
        expected = hmac.new(_TOKEN_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = encoded + '=' * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get('exp', 0) < time.time():
            return None
        return payload.get('sub')
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def auth_config() -> dict[str, str | bool]:
    return {'persistent': True, 'development_secret': APP_ENV == 'development'}