"""Community signals and user vibe preferences as ranking inputs.

Two separate signals feed the existing ranker (weights in ranking.json):

* ``vibe``      — how well a place's vibe profile matches *this* traveller's demonstrated
                  preferences, minus their personal aversions (crowded, expensive…) weighted by how
                  strongly visitors report those characteristics for the place. User-specific.
* ``community`` — general feedback-derived quality (shrunk average rating). Place-level.

Both are 0.5 (neutral) when there is no evidence, so places and travellers without feedback are
ranked exactly as before. Community signals are probabilistic opinions, reported as such, and
never mixed into place facts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from app.db.models import Feedback, PlaceAspectSignal, PlaceFeedbackSummary, PlaceVibeProfile, UserAspectPreference, UserVibePreference
from app.db.session import SessionLocal
from app.feedback.aggregate import aspect_prior, vibe_prior
from app.feedback.vocabulary import config, load_vocabulary


@dataclass
class UserVibes:
    user_id: str | None
    affinity: dict[str, float] = field(default_factory=dict)  # vibe → 0..1 (0.5 neutral)
    aversion: dict[str, float] = field(default_factory=dict)  # aspect → 0..1
    feedback_count: int = 0
    confidence: float = 0.0
    status: str = "cold_start"  # cold_start | emerging | established

    def as_dict(self, top: int | None = None) -> dict[str, Any]:
        likes = sorted(((k, v) for k, v in self.affinity.items() if v > 0.5), key=lambda kv: -kv[1])
        dislikes = sorted(((k, v) for k, v in self.affinity.items() if v < 0.5), key=lambda kv: kv[1])
        return {
            "status": self.status, "feedback_count": self.feedback_count, "confidence": round(self.confidence, 3),
            "vibe_preferences": dict(sorted(self.affinity.items(), key=lambda kv: -kv[1])),
            "liked_vibes": [k for k, _ in likes[:top]] if top else [k for k, _ in likes],
            "disliked_vibes": [k for k, _ in dislikes[:top]] if top else [k for k, _ in dislikes],
            "aversions": dict(sorted(self.aversion.items(), key=lambda kv: -kv[1])),
        }


@dataclass
class PlaceCommunity:
    place_id: str
    vibes: dict[str, float] = field(default_factory=dict)  # vibe → 0..1 association
    aspects: dict[str, float] = field(default_factory=dict)  # aspect → share of visitors reporting it
    feedback_count: int = 0
    confidence: float = 0.0
    bayes_rating: float | None = None
    recommend_share: float | None = None
    synthetic_share: float = 0.0
    from_feedback: bool = False  # False: only the place's own tags suggest these (no visitor feedback yet)

    def top_vibes(self, n: int = 3, minimum: float = 0.4) -> list[str]:
        return [k for k, v in sorted(self.vibes.items(), key=lambda kv: -kv[1]) if v >= minimum][:n]

    def as_dict(self) -> dict[str, Any]:
        labels = {v.key: v.label for v in load_vocabulary().vibes.values()} | {a.key: a.label for a in load_vocabulary().aspects.values()}
        return {
            "place_id": self.place_id, "kind": "community_signal",
            "note": "What visitors report — opinions, not verified facts." + (" Includes synthetic sample feedback used for development." if self.synthetic_share else ""),
            "feedback_count": self.feedback_count, "confidence": round(self.confidence, 3), "from_feedback": self.from_feedback,
            "rating": self.bayes_rating, "recommend_share": self.recommend_share, "synthetic_share": self.synthetic_share,
            "vibe_profile": {k: round(v, 3) for k, v in sorted(self.vibes.items(), key=lambda kv: -kv[1])},
            "top_vibes": [{"key": k, "label": labels.get(k, k)} for k in self.top_vibes()],
            "reported": [{"key": k, "label": labels.get(k, k), "share": round(v, 3)} for k, v in sorted(self.aspects.items(), key=lambda kv: -kv[1]) if v >= 0.2][:5],
        }


def user_vibes(user_id: str | None, profile: dict[str, Any] | None = None) -> UserVibes:
    """The traveller's vibe profile. Cold start: no feedback → a faint prior from their stated interests."""
    prefs = config()["preferences"]
    result = UserVibes(user_id=user_id)
    if user_id:
        vocab = load_vocabulary()
        vibes, aspects = vocab.vibe_by_id(), vocab.aspect_by_id()
        with SessionLocal() as db:
            result.feedback_count = db.scalar(select(func.count(func.distinct(Feedback.place_id))).where(Feedback.user_id == user_id)) or 0
            for row in db.scalars(select(UserVibePreference).where(UserVibePreference.user_id == user_id)).all():
                if row.vibe_id in vibes and vibes[row.vibe_id].status == "active":
                    result.affinity[vibes[row.vibe_id].key] = row.affinity
            for row in db.scalars(select(UserAspectPreference).where(UserAspectPreference.user_id == user_id)).all():
                if row.aspect_id in aspects:
                    result.aversion[aspects[row.aspect_id].key] = row.aversion
    result.confidence = result.feedback_count / (result.feedback_count + prefs["shrinkage"])
    result.status = "cold_start" if result.feedback_count < prefs["cold_start_below"] else "established" if result.feedback_count >= prefs["established_at"] else "emerging"
    # Stated interests give a faint starting point that real feedback quickly outweighs.
    weight = prefs["interest_prior_weight"] * (1 - result.confidence)
    for interest in (profile or {}).get("interests") or {}:
        for vibe in prefs["interest_vibes"].get(interest, []):
            result.affinity[vibe] = min(1.0, result.affinity.get(vibe, 0.5) + 0.5 * weight)
    return result


def place_communities(places: list[Any]) -> dict[str, PlaceCommunity]:
    """Community signals for places (candidates or POI rows); tags/category priors when no feedback exists."""
    ids = [p.id for p in places if getattr(p, "id", None)]
    out = {pid: PlaceCommunity(place_id=pid) for pid in ids}
    if not ids:
        return out
    vocab = load_vocabulary()
    vibes, aspects = vocab.vibe_by_id(), vocab.aspect_by_id()
    with SessionLocal() as db:
        for row in db.scalars(select(PlaceVibeProfile).where(PlaceVibeProfile.place_id.in_(ids))).all():
            if row.vibe_id in vibes:
                out[row.place_id].vibes[vibes[row.vibe_id].key] = row.score
        for row in db.scalars(select(PlaceAspectSignal).where(PlaceAspectSignal.place_id.in_(ids))).all():
            if row.aspect_id in aspects:
                out[row.place_id].aspects[aspects[row.aspect_id].key] = row.rate
        for row in db.scalars(select(PlaceFeedbackSummary).where(PlaceFeedbackSummary.place_id.in_(ids))).all():
            community = out[row.place_id]
            community.feedback_count, community.confidence = row.feedback_count, row.confidence
            community.bayes_rating, community.recommend_share, community.synthetic_share = row.bayes_rating, row.recommend_share, row.synthetic_share
            community.from_feedback = row.feedback_count > 0
    for place in places:
        community = out.get(getattr(place, "id", None))
        if community is None or community.from_feedback:
            continue
        # No visitor feedback yet: what the place's own tags/category suggest, at zero confidence.
        community.vibes = {v.key: vibe_prior(place, v.key) for v in vocab.active_vibes.values()}
        community.aspects = {a.key: p for a in aspects.values() if (p := aspect_prior(place, a.key))}
    return out


def vibe_compatibility(community: PlaceCommunity, user: UserVibes) -> tuple[float, list[str], dict[str, Any]]:
    """0..1 (0.5 neutral): the traveller's vibe preferences against the place's vibe profile, minus aversions."""
    rules = config()["ranking"]
    labels = {v.key: v.label.lower() for v in load_vocabulary().vibes.values()}
    centred = {k: a - 0.5 for k, a in user.affinity.items() if abs(a - 0.5) > 0.01}
    detail: dict[str, Any] = {"match": 0.0, "penalty": 0.0}
    if not centred and not user.aversion:
        return 0.5, [], detail
    strength = max(user.confidence, 0.15 if centred else 0.0)
    match = 0.0
    if centred:
        # A place "has" a vibe above the neutral association and lacks it below, so a place that is
        # clearly not peaceful counts against a traveller who loves peaceful places.
        neutral = rules["association_neutral"]
        match = sum(c * (community.vibes.get(k, 0.0) - neutral) for k, c in centred.items()) / sum(abs(c) for c in centred.values())
        match = max(-1.0, min(1.0, match * 2 / max(neutral, 1 - neutral)))
    penalty = min(rules["max_aversion_penalty"], rules["aversion_penalty"] * sum(level * community.aspects.get(aspect, 0.0) for aspect, level in user.aversion.items()))
    score = min(1.0, max(0.0, 0.5 + 0.5 * match * strength - penalty))
    detail.update(match=round(match, 3), penalty=round(penalty, 3), strength=round(strength, 3))
    reasons = []
    shared = [k for k, c in sorted(centred.items(), key=lambda kv: -kv[1]) if c > 0 and community.vibes.get(k, 0) >= 0.4][:2]
    if match * strength >= rules["reason_min_compat"] and shared:
        reasons.append("Matches vibes you've enjoyed: " + ", ".join(labels.get(k, k) for k in shared))
    worst = max(((a, level * community.aspects.get(a, 0.0)) for a, level in user.aversion.items()), key=lambda kv: kv[1], default=(None, 0))
    if worst[0] and worst[1] >= rules["reason_min_aversion"]:
        reasons.append(f"Visitors often report \"{aspect_labels().get(worst[0], worst[0]).lower()}\", which you've flagged before")
    return round(score, 4), reasons, detail


def community_quality(community: PlaceCommunity) -> tuple[float, list[str]]:
    """0..1 (0.5 neutral) from visitors' shrunk average rating; a general, place-level signal."""
    if not community.from_feedback or community.bayes_rating is None:
        return 0.5, []
    normalised = (community.bayes_rating - 1) / 4
    score = 0.5 + (normalised - 0.5) * community.confidence
    reasons = []
    if community.confidence >= config()["ranking"]["community_reason_min_confidence"]:
        labels = {v.key: v.label for v in load_vocabulary().vibes.values()}
        top = community.top_vibes(2, 0.5)
        reasons.append(f"Visitors rate it {community.bayes_rating:.1f}/5" + (f" and call it {' · '.join(labels.get(k, k).lower() for k in top)}" if top else "") + f" ({community.feedback_count} reviews)")
    return round(score, 4), reasons


def aspect_labels() -> dict[str, str]:
    return {a.key: a.label for a in load_vocabulary().aspects.values()}


def load_json(value: str | None) -> Any:
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None
