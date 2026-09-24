from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.config import APP_ENV
from app.services.profile import resolve_profile
from app.services.query_service import AskRequest, query_service

router = APIRouter(prefix="/api")


@router.post("/ask")
def ask(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    safe = payload or {}
    question = str(safe.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="A question is required.")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Please keep questions under 500 characters.")
    profile = resolve_profile(authorization, safe.get("profile") if isinstance(safe.get("profile"), dict) else None)
    language = str(safe.get("language") or profile.get("language") or "en")
    request = AskRequest(
        question=question,
        user_location=safe.get("user_location") if isinstance(safe.get("user_location"), dict) else None,
        active_destination=safe.get("active_destination"),
        selected_place_id=str(safe.get("selected_place_id")) if safe.get("selected_place_id") else None,
        profile=profile,
        language=language if language in {"en", "kn", "hi"} else "en",
        debug=bool(safe.get("debug")) and APP_ENV != "production",
        selected_date=str(safe.get("date"))[:10] if safe.get("date") else None,
    )
    return query_service.ask(request)
