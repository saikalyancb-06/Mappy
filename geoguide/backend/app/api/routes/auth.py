from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.rules import taxonomy
from app.db.models import InteractionEvent, User, UserPreference, utcnow
from app.db.session import SessionLocal
from app.services.auth import auth_config, hash_password, issue_token, verify_password
from app.services.profile import preference_dict, user_from_authorization

router = APIRouter(prefix="/api")

_ALLOWED = {"budget": {"low", "moderate", "high"}, "pace": {"relaxed", "balanced", "packed"}, "walking": {"low", "moderate", "high"}, "language": {"en", "kn", "hi"}}


def _auth_user(authorization: str | None) -> User:
    user = user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return user


def _preference(db, user_id: str) -> UserPreference:
    preference = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    if preference is None:
        preference = UserPreference(id=uuid.uuid4().hex, user_id=user_id)
        db.add(preference)
        db.flush()
    return preference


@router.post("/auth/signup")
def signup(payload: dict | None) -> dict:
    safe = payload or {}
    email = str(safe.get("email", "")).strip().lower()
    password = str(safe.get("password", ""))
    name = str(safe.get("name", "")).strip() or "Traveller"
    if "@" not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail="Use a valid email and a password with at least 6 characters.")
    user = User(id=uuid.uuid4().hex, email=email, name=name, password_hash=hash_password(password))
    try:
        with SessionLocal() as db:
            db.add(user)
            db.commit()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from None
    return {"token": issue_token(user.id), "user": {"id": user.id, "email": user.email, "name": user.name}, "auth": auth_config()}


@router.post("/auth/login")
def login(payload: dict | None) -> dict:
    safe = payload or {}
    email = str(safe.get("email", "")).strip().lower()
    password = str(safe.get("password", ""))
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        return {"token": issue_token(user.id), "user": {"id": user.id, "email": user.email, "name": user.name}, "auth": auth_config()}


@router.get("/auth/me")
def current_user(authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    return {"user": {"id": user.id, "email": user.email, "name": user.name}}


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    _auth_user(authorization)
    return {"status": "ok"}


@router.get("/preferences")
def get_preferences(authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    with SessionLocal() as db:
        preference = _preference(db, user.id)
        db.commit()
        return preference_dict(preference)


@router.put("/preferences")
def update_preferences(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    safe = payload or {}
    valid_interests = set(taxonomy()["groups"]) | set(taxonomy()["categories"])
    with SessionLocal() as db:
        preference = _preference(db, user.id)
        for key, allowed in _ALLOWED.items():
            value = safe.get(key)
            if isinstance(value, str) and value in allowed:
                setattr(preference, key, value)
        if isinstance(safe.get("interests"), (dict, list)):
            interests = safe["interests"] if isinstance(safe["interests"], dict) else {item: 1 for item in safe["interests"]}
            preference.interests = json.dumps({k: float(v) for k, v in interests.items() if k in valid_interests})
        if isinstance(safe.get("accessibility"), list):
            preference.accessibility = json.dumps([item for item in safe["accessibility"] if item in taxonomy()["preferences"]])
        for key in ("likes", "dislikes"):
            if isinstance(safe.get(key), list):
                setattr(preference, key, json.dumps([str(item) for item in safe[key]][:200]))
        preference.updated_at = utcnow()
        db.commit()
        return preference_dict(preference)


@router.post("/interactions")
def record_interaction(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = _auth_user(authorization)
    safe = payload or {}
    event_type = str(safe.get("event_type") or "").strip()
    if event_type not in {"clicked", "saved", "unsaved", "dismissed", "liked", "disliked"}:
        raise HTTPException(status_code=422, detail="Unsupported interaction type.")
    poi_id = str(safe.get("poi_id") or "") or None
    with SessionLocal() as db:
        db.add(InteractionEvent(id=uuid.uuid4().hex, user_id=user.id, poi_id=poi_id, event_type=event_type))
        category = str(safe.get("category") or "").strip().casefold()
        preference = _preference(db, user.id)
        if category in taxonomy()["categories"] and event_type in {"saved", "unsaved"}:
            interests = json.loads(preference.interests or "{}")
            delta = 0.25 if event_type == "saved" else -0.1
            interests[category] = round(max(0.0, min(5.0, float(interests.get(category, 0)) + delta)), 2)
            preference.interests = json.dumps(interests)
        if poi_id and event_type in {"liked", "disliked"}:
            key = "likes" if event_type == "liked" else "dislikes"
            other = "dislikes" if key == "likes" else "likes"
            values = [v for v in json.loads(getattr(preference, key) or "[]") if v != poi_id] + [poi_id]
            setattr(preference, key, json.dumps(values[-200:]))
            setattr(preference, other, json.dumps([v for v in json.loads(getattr(preference, other) or "[]") if v != poi_id]))
        db.commit()
    return {"status": "recorded"}
