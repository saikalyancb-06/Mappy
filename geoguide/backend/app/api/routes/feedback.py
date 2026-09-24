"""Feedback API: vocabulary, submit, community signals, the traveller's vibe profile, and dev analytics."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import select

from app.config import APP_ENV
from app.db.models import Feedback, Poi
from app.db.session import SessionLocal
from app.feedback.analytics import analytics
from app.feedback.service import FeedbackError, feedback_dict, parse_input, submit_feedback
from app.feedback.signals import aspect_labels, place_communities, user_vibes
from app.feedback.vocabulary import load_vocabulary, public_vocabulary
from app.services.profile import resolve_profile, user_from_authorization

router = APIRouter(prefix="/api")


@router.get("/feedback/vocabulary")
def vocabulary() -> dict:
    return public_vocabulary()


@router.post("/feedback")
def create_feedback(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    user = user_from_authorization(authorization)
    try:
        data = parse_input(payload or {})
        saved = submit_feedback(data, user.id if user else None)
    except FeedbackError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_feedback", **exc.as_dict()}) from None
    with SessionLocal() as db:
        place = db.get(Poi, data.place_id)
    community = place_communities([place])[place.id].as_dict() if place else None
    return {"feedback": saved, "community": community, "your_vibes": _public_profile(user.id, resolve_profile(authorization)) if user else None, "message": "Thanks — this shapes what GeoGuide suggests next."}


@router.get("/places/{place_id}/community")
def place_community(place_id: str) -> dict:
    with SessionLocal() as db:
        place = db.get(Poi, place_id)
        if place is None:
            raise HTTPException(status_code=404, detail="Unknown place.")
        recent = db.scalars(select(Feedback).where(Feedback.place_id == place_id, Feedback.text_feedback.is_not(None)).order_by(Feedback.created_at.desc()).limit(3)).all()
        quotes = [{"text": row.text_feedback, "rating": row.overall_rating, "synthetic": row.is_synthetic, "date": (row.visit_date or row.created_at.date().isoformat())} for row in recent]
    return {**place_communities([place])[place.id].as_dict(), "recent": quotes}


def _public_profile(user_id: str, profile: dict) -> dict:
    """What a traveller sees about their own profile: labels, not internal scores."""
    vibes = user_vibes(user_id, profile)
    labels = {v.key: {"key": v.key, "label": v.label, "emoji": v.emoji} for v in load_vocabulary().vibes.values()}
    aspects = aspect_labels()
    data = vibes.as_dict(top=5)
    return {
        "status": data["status"], "feedback_count": data["feedback_count"],
        "liked_vibes": [labels[k] for k in data["liked_vibes"] if k in labels],
        "disliked_vibes": [labels[k] for k in data["disliked_vibes"] if k in labels],
        "avoids": [{"key": k, "label": aspects.get(k, k)} for k, v in data["aversions"].items() if v >= 0.15][:5],
    }


@router.get("/me/vibes")
def my_vibes(authorization: str | None = Header(default=None)) -> dict:
    user = user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Log in to see your vibe profile.")
    return _public_profile(user.id, resolve_profile(authorization))


@router.get("/me/feedback")
def my_feedback(authorization: str | None = Header(default=None), limit: int = Query(20, ge=1, le=100)) -> dict:
    user = user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Log in to see your feedback.")
    with SessionLocal() as db:
        rows = db.scalars(select(Feedback).where(Feedback.user_id == user.id).order_by(Feedback.created_at.desc()).limit(limit)).all()
        return {"items": [feedback_dict(db, row) for row in rows]}


@router.get("/feedback/analytics")
def feedback_analytics(destination_id: str | None = None, user_id: str | None = None, limit: int = Query(10, ge=1, le=50)) -> dict:
    """Internal analytics for the development team (disabled in production)."""
    if APP_ENV == "production":
        raise HTTPException(status_code=404, detail="Not found.")
    return analytics(destination_id=destination_id, user_id=user_id, limit=limit)
