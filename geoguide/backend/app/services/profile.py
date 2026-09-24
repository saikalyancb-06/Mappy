"""Traveller profile resolution (stored preferences + per-request overrides)."""
from __future__ import annotations

import json
from typing import Any

from app.db.models import User, UserPreference
from app.db.session import SessionLocal
from app.services.auth import verify_token

DEFAULT_PROFILE: dict[str, Any] = {"interests": {}, "budget": None, "pace": None, "walking": None, "accessibility": [], "likes": [], "dislikes": [], "language": "en"}


def user_from_authorization(authorization: str | None) -> User | None:
    if not authorization:
        return None
    user_id = verify_token(authorization.removeprefix("Bearer ").strip())
    if not user_id:
        return None
    with SessionLocal() as db:
        return db.get(User, user_id)


def preference_dict(preference: UserPreference) -> dict[str, Any]:
    def load(value: str | None, default: Any) -> Any:
        try:
            return json.loads(value) if value else default
        except ValueError:
            return default

    return {
        "interests": load(preference.interests, {}),
        "budget": preference.budget,
        "pace": preference.pace,
        "walking": preference.walking,
        "accessibility": load(preference.accessibility, []),
        "likes": load(preference.likes, []),
        "dislikes": load(preference.dislikes, []),
        "language": preference.language or "en",
        "updated_at": preference.updated_at.isoformat() if preference.updated_at else None,
    }


def resolve_profile(authorization: str | None, override: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = dict(DEFAULT_PROFILE)
    user = user_from_authorization(authorization)
    if user:
        with SessionLocal() as db:
            preference = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
        if preference:
            profile.update(preference_dict(preference))
    for key, value in (override or {}).items():
        if key in DEFAULT_PROFILE and value not in (None, ""):
            profile[key] = value
    if isinstance(profile.get("interests"), list):
        profile["interests"] = {item: 1 for item in profile["interests"]}
    return profile
