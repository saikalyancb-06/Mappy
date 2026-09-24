"""Feedback service: validate → store (selected + derived signals) → update place and user profiles.

The traveller's explicit choices are the authoritative signal. Text analysis adds *derived*
links at a lower weight and never removes or changes what they selected.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import Feedback, FeedbackAspect, FeedbackVibe, Poi
from app.db.session import SessionLocal
from app.feedback.aggregate import recompute_place, recompute_user
from app.feedback.nlp import analyse
from app.feedback.vocabulary import InvalidCustomVibe, config, ensure_custom_vibe, load_vocabulary, normalize_custom_vibe
from app.geo.geo_context import destination_by_id

MAX_TEXT = 2000


class FeedbackError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field, self.message = field, message

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "message": self.message}


@dataclass
class FeedbackInput:
    place_id: str
    overall_rating: int
    vibes: list[str]
    custom_vibes: list[str]
    liked_aspects: list[str]
    disliked_aspects: list[str]
    text_feedback: str | None = None
    visit_date: str | None = None
    crowd_level: str | None = None
    price_level: str | None = None
    scores: dict[str, int | None] | None = None
    place_name: str | None = None  # only for places GeoGuide doesn't store (e.g. a live search result)
    category: str | None = None


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise FeedbackError("list", "Expected a list.")
    return [str(item).strip() for item in value if str(item).strip()]


def parse_input(payload: dict[str, Any]) -> FeedbackInput:
    cfg = config()
    place_id = str(payload.get("place_id") or "").strip()
    if not place_id:
        raise FeedbackError("place_id", "Which place is this feedback for?")
    try:
        rating = int(payload.get("overall_rating"))
    except (TypeError, ValueError):
        raise FeedbackError("overall_rating", "Choose how the visit was (1–5).") from None
    if not 1 <= rating <= 5:
        raise FeedbackError("overall_rating", "The rating must be between 1 and 5.")
    text = payload.get("text_feedback")
    if text is not None and len(str(text)) > MAX_TEXT:
        raise FeedbackError("text_feedback", f"Please keep it under {MAX_TEXT} characters.")
    visit = payload.get("visit_date")
    if visit:
        try:
            visit_day = date.fromisoformat(str(visit)[:10])
        except ValueError:
            raise FeedbackError("visit_date", "Use a date like 2026-09-24.") from None
        if visit_day > datetime.now(timezone.utc).date() + timedelta(days=1):
            raise FeedbackError("visit_date", "The visit date can't be in the future.")
    for field, allowed in (("crowd_level", cfg["crowd_levels"]), ("price_level", cfg["price_levels"])):
        if payload.get(field) not in (None, "") and payload[field] not in allowed:
            raise FeedbackError(field, f"{field} must be one of {allowed}.")
    scores: dict[str, int | None] = {}
    for field in cfg["score_fields"]:
        value = payload.get(field)
        if value in (None, ""):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise FeedbackError(field, f"{field} must be 1–5.") from None
        if not 1 <= number <= 5:
            raise FeedbackError(field, f"{field} must be 1–5.")
        scores[field] = number
    custom = _list(payload.get("custom_vibes"))
    if len(custom) > cfg["custom_vibes"]["max_per_feedback"]:
        raise FeedbackError("custom_vibes", f"Add at most {cfg['custom_vibes']['max_per_feedback']} custom vibes.")
    return FeedbackInput(
        place_id=place_id, overall_rating=rating, vibes=_list(payload.get("vibes")), custom_vibes=custom,
        liked_aspects=_list(payload.get("liked_aspects")), disliked_aspects=_list(payload.get("disliked_aspects")),
        text_feedback=str(text).strip() if text else None, visit_date=str(visit)[:10] if visit else None,
        crowd_level=payload.get("crowd_level") or None, price_level=payload.get("price_level") or None, scores=scores,
        place_name=str(payload.get("place_name") or "").strip()[:200] or None, category=str(payload.get("category") or "").strip()[:60] or None,
    )


def submit_feedback(data: FeedbackInput, user_id: str | None, *, synthetic: bool = False, feedback_id: str | None = None, created_at: datetime | None = None, recompute: bool = True) -> dict[str, Any]:
    cfg = config()
    vocab = load_vocabulary()
    active = vocab.active_vibes
    unknown = [key for key in data.vibes if key not in active]
    if unknown:
        raise FeedbackError("vibes", f"Unknown vibe(s): {', '.join(unknown)}. Use 'Other' for a vibe that isn't listed.")
    for field, polarity, keys in (("liked_aspects", "positive", data.liked_aspects), ("disliked_aspects", "negative", data.disliked_aspects)):
        bad = [key for key in keys if key not in vocab.aspects or vocab.aspects[key].polarity != polarity]
        if bad:
            raise FeedbackError(field, f"Unknown {polarity} aspect(s): {', '.join(bad)}.")
    custom_parsed = []
    for text in data.custom_vibes:
        try:
            custom_parsed.append(normalize_custom_vibe(text))
        except InvalidCustomVibe as exc:
            raise FeedbackError("custom_vibes", str(exc)) from None

    with SessionLocal() as db:
        place = db.get(Poi, data.place_id)
        if place is None and not data.place_name:
            raise FeedbackError("place_id", "That place isn't known to GeoGuide; include its name.")
        destination = destination_by_id(place.destination_id) if place else None
        signals = analyse(data.text_feedback, data.overall_rating)
        # One opinion per traveller, place and day: a resubmission replaces the earlier one.
        existing = None
        if user_id and not synthetic:
            since = datetime.now(timezone.utc) - timedelta(days=1)
            existing = db.scalars(select(Feedback).where(Feedback.user_id == user_id, Feedback.place_id == data.place_id, Feedback.created_at >= since.replace(tzinfo=None))).first()
        row = existing or Feedback(id=feedback_id or f"fb_{uuid.uuid4().hex[:16]}")
        row.user_id = user_id
        row.place_id = data.place_id
        row.place_name = place.name if place else data.place_name
        row.city = destination.name if destination else None
        row.destination_id = place.destination_id if place else None
        row.category = place.category if place else data.category
        row.visit_date = data.visit_date
        row.overall_rating = data.overall_rating
        row.recommendation = cfg["recommendation"][str(data.overall_rating)]
        row.crowd_level, row.price_level = data.crowd_level, data.price_level
        for field in cfg["score_fields"]:
            setattr(row, field, (data.scores or {}).get(field))
        row.text_feedback = data.text_feedback
        row.sentiment, row.sentiment_score = signals.sentiment, signals.sentiment_score
        row.derived = json.dumps(signals.as_dict())
        row.is_synthetic, row.source = synthetic, "synthetic_bootstrap" if synthetic else "user"
        if created_at:
            row.created_at = created_at.replace(tzinfo=None)
        if existing is None:
            db.add(row)
        db.flush()
        db.query(FeedbackVibe).filter(FeedbackVibe.feedback_id == row.id).delete()
        db.query(FeedbackAspect).filter(FeedbackAspect.feedback_id == row.id).delete()

        derived_weight = cfg["aggregation"]["derived_weight"]
        contributions: dict[str, float] = {}
        for key in data.vibes:
            db.add(FeedbackVibe(feedback_id=row.id, vibe_id=active[key].id, origin="selected", weight=1.0))
            contributions[key] = 1.0
        for key, label in custom_parsed:
            vibe = active.get(key) or ensure_custom_vibe(db, key, label)
            if vibe.key not in contributions:
                db.add(FeedbackVibe(feedback_id=row.id, vibe_id=vibe.id, origin="custom" if vibe.origin == "custom" else "selected", weight=1.0))
                contributions[vibe.key] = 1.0
        for key in signals.vibes:
            if key not in contributions and key in active:
                db.add(FeedbackVibe(feedback_id=row.id, vibe_id=active[key].id, origin="derived", weight=derived_weight))
                contributions[key] = derived_weight
        row.derived_vibe_scores = json.dumps(contributions)
        chosen = set()
        for key in [*data.liked_aspects, *data.disliked_aspects]:
            db.add(FeedbackAspect(feedback_id=row.id, aspect_id=vocab.aspects[key].id, origin="selected", weight=1.0))
            chosen.add(key)
        for key in [*signals.liked_aspects, *signals.disliked_aspects]:
            if key not in chosen and key in vocab.aspects:
                db.add(FeedbackAspect(feedback_id=row.id, aspect_id=vocab.aspects[key].id, origin="derived", weight=derived_weight))
                chosen.add(key)
        db.commit()
        result = feedback_dict(db, row)
    if recompute:
        recompute_place(data.place_id)
        recompute_user(user_id)
    return result


def feedback_dict(db, row: Feedback) -> dict[str, Any]:
    vocab = load_vocabulary()
    vibes, aspects = vocab.vibe_by_id(), vocab.aspect_by_id()
    vibe_links = db.scalars(select(FeedbackVibe).where(FeedbackVibe.feedback_id == row.id)).all()
    aspect_links = db.scalars(select(FeedbackAspect).where(FeedbackAspect.feedback_id == row.id)).all()

    def keys(links, origin, polarity=None, table=None):
        return [table[link.vibe_id if polarity is None else link.aspect_id].key for link in links if link.origin in origin and (polarity is None or table[link.aspect_id].polarity == polarity)]

    return {
        "feedback_id": row.id, "user_id": row.user_id, "place_id": row.place_id, "place_name": row.place_name, "city": row.city, "category": row.category,
        "visit_date": row.visit_date, "overall_rating": row.overall_rating, "recommendation": row.recommendation,
        "vibes": keys(vibe_links, {"selected", "custom"}, table=vibes), "derived_vibes": keys(vibe_links, {"derived"}, table=vibes),
        "liked_aspects": keys(aspect_links, {"selected"}, "positive", aspects), "disliked_aspects": keys(aspect_links, {"selected"}, "negative", aspects),
        "derived_aspects": keys(aspect_links, {"derived"}, "positive", aspects) + keys(aspect_links, {"derived"}, "negative", aspects),
        "crowd_level": row.crowd_level, "price_level": row.price_level,
        **{field: getattr(row, field) for field in config()["score_fields"]},
        "text_feedback": row.text_feedback, "sentiment": row.sentiment, "sentiment_score": row.sentiment_score,
        "derived": json.loads(row.derived) if row.derived else None,
        "derived_vibe_scores": json.loads(row.derived_vibe_scores) if row.derived_vibe_scores else {},
        "is_synthetic": row.is_synthetic, "source": row.source,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }
