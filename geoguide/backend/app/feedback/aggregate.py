"""Feedback → place vibe profiles, place-level signals and user preference profiles.

Place vibe score (0..1), per vibe v:

    score_v = (Σ w_f · s_fv + k · prior_v) / (Σ w_f + k)

* w_f   = recency decay (half-life) × rating weight of feedback f
* s_fv  = 1 when the visitor selected v, ``derived_weight`` when only their text suggested it
* prior = what the place's own tags/category suggest (tiny when nothing does)
* k     = prior strength: with few reviews the prior dominates, so one review can't redefine a place
* confidence = Σ w_f / (Σ w_f + k)

User affinity (0.5 neutral), per vibe v, over the user's latest feedback per place:

    affinity_v = 0.5 + 0.5 · Σ w_f · valence_f · s_fv / (Σ w_f + shrinkage),   valence = (rating − 3) / 2

so liking places with v raises it, disliking them lowers it, and one review moves it only a little.
User aversions (0..1) to characteristics come from the negative aspects they report (and from
positives like "less crowded", which say they value the opposite). Aversions are user-specific;
place-level quality comes only from the aggregate of everyone's ratings.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.config import FEEDBACK_INCLUDE_SYNTHETIC
from app.db.models import (
    Feedback,
    FeedbackAspect,
    FeedbackVibe,
    PlaceAspectSignal,
    PlaceFeedbackSummary,
    PlaceReviewSignal,
    PlaceVibeProfile,
    Poi,
    UserAspectPreference,
    UserVibePreference,
    utcnow,
)
from app.db.session import SessionLocal
from app.feedback.vocabulary import config, load_vocabulary


def _age_days(feedback: Feedback, now: datetime) -> float:
    when: datetime | None = None
    if feedback.visit_date:
        try:
            when = datetime.combine(date.fromisoformat(feedback.visit_date[:10]), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            when = None
    if when is None and feedback.created_at:
        when = feedback.created_at.replace(tzinfo=timezone.utc) if feedback.created_at.tzinfo is None else feedback.created_at
    return max(0.0, (now - when).total_seconds() / 86400) if when else 0.0


def decay(age_days: float, half_life_days: float) -> float:
    return math.pow(0.5, age_days / half_life_days) if half_life_days > 0 else 1.0


def _tags(poi: Any) -> set[str]:
    raw = getattr(poi, "tags", None)
    if isinstance(raw, list):
        return set(raw)
    try:
        return set(json.loads(raw or "[]"))
    except (TypeError, ValueError):
        return set()


def vibe_prior(place: Any, vibe_key: str) -> float:
    """What the place's own tags/category suggest about a vibe, before any feedback."""
    cfg = config()
    agg = cfg["aggregation"]
    entry = next((v for v in cfg["vibes"] if v["key"] == vibe_key), None)
    if place is None or entry is None:
        return agg["prior_base"]
    if _tags(place) & set(entry.get("place_tags", [])):
        return agg["prior_tag"]
    if getattr(place, "category", None) in entry.get("categories", []):
        return agg["prior_category"]
    return agg["prior_base"]


def aspect_prior(place: Any, aspect_key: str) -> float:
    """Negative characteristics a place's own data suggests (a 'crowded' tag, a high price level)."""
    entry = next((a for a in config()["aspects"] if a["key"] == aspect_key), None)
    if place is None or entry is None or entry["polarity"] != "negative":
        return 0.0
    prior = config()["aggregation"]["negative_prior_from_tags"]
    if _tags(place) & set(entry.get("place_tags", [])):
        return prior
    if entry.get("price_level_at_least") and (getattr(place, "price_level", None) or 0) >= entry["price_level_at_least"]:
        return prior
    return 0.0


def _feedback_filter(statement):
    return statement if FEEDBACK_INCLUDE_SYNTHETIC else statement.where(Feedback.is_synthetic.is_(False))


def recompute_place(place_id: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    cfg = config()
    agg = cfg["aggregation"]
    vocab = load_vocabulary()
    vibes_by_id, aspects_by_id = vocab.vibe_by_id(), vocab.aspect_by_id()
    with SessionLocal() as db:
        place = db.get(Poi, place_id)
        feedbacks = db.scalars(_feedback_filter(select(Feedback).where(Feedback.place_id == place_id))).all()
        ids = [f.id for f in feedbacks]
        vibe_links = db.scalars(select(FeedbackVibe).where(FeedbackVibe.feedback_id.in_(ids))).all() if ids else []
        aspect_links = db.scalars(select(FeedbackAspect).where(FeedbackAspect.feedback_id.in_(ids))).all() if ids else []
        weight = {f.id: decay(_age_days(f, now), agg["place_half_life_days"]) * agg["rating_weight"][str(f.overall_rating)] for f in feedbacks}
        total = sum(weight.values())
        selections: dict[int, float] = defaultdict(float)
        support: dict[int, int] = defaultdict(int)
        for link in vibe_links:
            selections[link.vibe_id] += weight[link.feedback_id] * link.weight
            support[link.vibe_id] += 1
        # Provider reviews are extra, down-weighted evidence (capped), never a replacement for GeoGuide feedback.
        review_rows = db.scalars(select(PlaceReviewSignal).where(PlaceReviewSignal.poi_id == place_id)).all()
        reviews_read = max((row.reviews_read for row in review_rows), default=0)
        review_total = agg.get("review_weight", 0.3) * min(reviews_read, agg.get("review_cap", 20))
        vibe_ids = {v.key: vid for vid, v in vibes_by_id.items()}
        aspect_ids = {a.key: aid for aid, a in aspects_by_id.items()}
        review_aspects: dict[int, float] = defaultdict(float)
        for row in review_rows:
            share = review_total * min(1.0, row.evidence_count / max(1, reviews_read))
            if row.kind == "vibe" and row.key in vibe_ids and row.polarity > 0:
                selections[vibe_ids[row.key]] += share
                support[vibe_ids[row.key]] += row.evidence_count
            elif row.kind == "aspect" and row.key in aspect_ids and row.polarity < 0:
                review_aspects[aspect_ids[row.key]] += share
        total += review_total
        k = agg["prior_strength"]
        db.execute(delete(PlaceVibeProfile).where(PlaceVibeProfile.place_id == place_id))
        for vibe_id, vibe in vibes_by_id.items():
            if vibe.status != "active":
                continue
            prior = vibe_prior(place, vibe.key)
            if not support[vibe_id] and prior <= agg["prior_base"] and not feedbacks and not review_rows:
                continue
            db.add(PlaceVibeProfile(place_id=place_id, vibe_id=vibe_id, score=round((selections[vibe_id] + k * prior) / (total + k), 4), selections=round(selections[vibe_id], 4), support=support[vibe_id], confidence=round(total / (total + k), 4), updated_at=utcnow()))

        aspect_hits: dict[int, float] = defaultdict(float)
        aspect_support: dict[int, int] = defaultdict(int)
        for link in aspect_links:
            aspect_hits[link.aspect_id] += weight[link.feedback_id] * link.weight
            aspect_support[link.aspect_id] += 1
        for aspect_id, share in review_aspects.items():
            aspect_hits[aspect_id] += share
            aspect_support[aspect_id] += 1
        kn = agg["negative_prior_strength"]
        db.execute(delete(PlaceAspectSignal).where(PlaceAspectSignal.place_id == place_id))
        for aspect_id, aspect in aspects_by_id.items():
            prior = aspect_prior(place, aspect.key)
            if not aspect_support[aspect_id] and not prior:
                continue
            db.add(PlaceAspectSignal(place_id=place_id, aspect_id=aspect_id, rate=round((aspect_hits[aspect_id] + kn * prior) / (total + kn), 4), support=aspect_support[aspect_id], updated_at=utcnow()))

        summary = db.get(PlaceFeedbackSummary, place_id) or PlaceFeedbackSummary(place_id=place_id)
        m, mu = agg["rating_prior_strength"], agg["rating_prior_mean"]
        summary.feedback_count = len(feedbacks)
        summary.weighted_count = round(total, 4)
        summary.average_rating = round(sum(f.overall_rating for f in feedbacks) / len(feedbacks), 3) if feedbacks else None
        summary.bayes_rating = round((sum(weight[f.id] * f.overall_rating for f in feedbacks) + m * mu) / (total + m), 3) if feedbacks else None
        summary.recommend_share = round(sum(weight[f.id] for f in feedbacks if f.overall_rating >= 4) / total, 3) if total else None
        summary.confidence = round(total / (total + k), 4)
        summary.synthetic_share = round(sum(1 for f in feedbacks if f.is_synthetic) / len(feedbacks), 3) if feedbacks else 0.0
        summary.last_feedback_at = max((f.created_at for f in feedbacks), default=None)
        summary.updated_at = utcnow()
        db.merge(summary)
        db.commit()


def recompute_user(user_id: str | None, now: datetime | None = None) -> None:
    if not user_id:
        return
    now = now or datetime.now(timezone.utc)
    prefs = config()["preferences"]
    vocab = load_vocabulary()
    positives = {a["key"]: a.get("values_characteristic") for a in config()["aspects"] if a["polarity"] == "positive"}
    with SessionLocal() as db:
        rows = db.scalars(select(Feedback).where(Feedback.user_id == user_id).order_by(Feedback.created_at.desc())).all()
        latest: dict[str, Feedback] = {}
        for row in rows:
            latest.setdefault(row.place_id, row)  # one opinion per place: the most recent
        feedbacks = list(latest.values())
        ids = [f.id for f in feedbacks]
        vibe_links = db.scalars(select(FeedbackVibe).where(FeedbackVibe.feedback_id.in_(ids))).all() if ids else []
        aspect_links = db.scalars(select(FeedbackAspect).where(FeedbackAspect.feedback_id.in_(ids))).all() if ids else []
        weight = {f.id: decay(_age_days(f, now), prefs["half_life_days"]) for f in feedbacks}
        valence = {f.id: (f.overall_rating - 3) / 2 for f in feedbacks}
        total = sum(weight.values())
        shrink = prefs["shrinkage"]
        raw: dict[int, float] = defaultdict(float)
        evidence: dict[int, float] = defaultdict(float)
        support: dict[int, int] = defaultdict(int)
        for link in vibe_links:
            raw[link.vibe_id] += weight[link.feedback_id] * valence[link.feedback_id] * link.weight
            evidence[link.vibe_id] += weight[link.feedback_id] * link.weight
            support[link.vibe_id] += 1
        db.execute(delete(UserVibePreference).where(UserVibePreference.user_id == user_id))
        for vibe_id in support:
            affinity = min(1.0, max(0.0, 0.5 + 0.5 * raw[vibe_id] / (total + shrink)))
            db.add(UserVibePreference(user_id=user_id, vibe_id=vibe_id, affinity=round(affinity, 4), evidence=round(evidence[vibe_id], 4), support=support[vibe_id], updated_at=utcnow()))

        aspects = vocab.aspect_by_id()
        by_key = {a.key: a for a in aspects.values()}
        hits: dict[int, float] = defaultdict(float)
        a_support: dict[int, int] = defaultdict(int)
        for link in aspect_links:
            aspect = aspects.get(link.aspect_id)
            if aspect is None:
                continue
            if aspect.polarity == "negative":
                target, share = aspect, 1.0
            elif positives.get(aspect.key) and positives[aspect.key] in by_key:
                target, share = by_key[positives[aspect.key]], 0.5  # "less crowded" as a plus → values uncrowded places
            else:
                continue
            hits[target.id] += weight[link.feedback_id] * link.weight * share
            a_support[target.id] += 1
        db.execute(delete(UserAspectPreference).where(UserAspectPreference.user_id == user_id))
        for aspect_id, value in hits.items():
            db.add(UserAspectPreference(user_id=user_id, aspect_id=aspect_id, aversion=round(min(1.0, value / (total + shrink)), 4), support=a_support[aspect_id], updated_at=utcnow()))
        db.commit()


def recompute_all() -> dict[str, int]:
    with SessionLocal() as db:
        places = [row[0] for row in db.execute(select(Feedback.place_id).distinct()).all()]
        users = [row[0] for row in db.execute(select(Feedback.user_id).where(Feedback.user_id.is_not(None)).distinct()).all()]
    for place_id in places:
        recompute_place(place_id)
    for user_id in users:
        recompute_user(user_id)
    return {"places": len(places), "users": len(users)}
