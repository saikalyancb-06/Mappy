"""Internal analytics for the feedback / vibe system (development only)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select

from app.core.rules import taxonomy
from app.db.models import Feedback, FeedbackAspect, FeedbackVibe, PlaceFeedbackSummary, Vibe
from app.db.session import SessionLocal
from app.feedback.signals import place_communities, user_vibes
from app.feedback.vocabulary import load_vocabulary
from app.geo.geo_context import build_geo_context
from app.geo.spatial import get_pois
from app.services.discovery import DiscoveryRequest, discover


def analytics(destination_id: str | None = None, user_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    vocab = load_vocabulary()
    vibes, aspects = vocab.vibe_by_id(), vocab.aspect_by_id()
    with SessionLocal() as db:
        base = select(Feedback)
        if destination_id:
            base = base.where(Feedback.destination_id == destination_id)
        rows = db.scalars(base).all()
        ids = [r.id for r in rows]
        vibe_links = db.scalars(select(FeedbackVibe).where(FeedbackVibe.feedback_id.in_(ids))).all() if ids else []
        aspect_links = db.scalars(select(FeedbackAspect).where(FeedbackAspect.feedback_id.in_(ids))).all() if ids else []
        summaries = db.scalars(select(PlaceFeedbackSummary).order_by(PlaceFeedbackSummary.feedback_count.desc()).limit(200)).all()
        custom = db.scalars(select(Vibe).where(Vibe.origin == "custom").order_by(Vibe.use_count.desc()).limit(20)).all()
        per_user = db.execute(select(Feedback.user_id, func.count(func.distinct(Feedback.place_id))).where(Feedback.user_id.is_not(None)).group_by(Feedback.user_id)).all()
    selected = Counter(vibes[l.vibe_id].key for l in vibe_links if l.origin != "derived" and l.vibe_id in vibes)
    derived = Counter(vibes[l.vibe_id].key for l in vibe_links if l.origin == "derived" and l.vibe_id in vibes)
    disliked = Counter(aspects[l.aspect_id].key for l in aspect_links if l.aspect_id in aspects and aspects[l.aspect_id].polarity == "negative")
    liked = Counter(aspects[l.aspect_id].key for l in aspect_links if l.aspect_id in aspects and aspects[l.aspect_id].polarity == "positive")
    place_ids = {r.place_id for r in rows}
    top_summaries = [s for s in summaries if not destination_id or s.place_id in place_ids][:limit]
    pois = {c.id: c for c in get_pois([s.place_id for s in top_summaries])}
    communities = place_communities(list(pois.values()))
    buckets = Counter("cold_start" if n < 1 else "emerging" if n < 3 else "established" for _, n in per_user)
    result: dict[str, Any] = {
        "volume": {
            "feedback": len(rows), "from_users": sum(1 for r in rows if not r.is_synthetic), "synthetic": sum(1 for r in rows if r.is_synthetic),
            "with_text": sum(1 for r in rows if r.text_feedback), "places": len(place_ids), "users": len({r.user_id for r in rows if r.user_id}),
        },
        "ratings": dict(sorted(Counter(r.overall_rating for r in rows).items())),
        "sentiment": dict(Counter(r.sentiment for r in rows)),
        "most_selected_vibes": selected.most_common(15),
        "vibes_from_text_only": derived.most_common(10),
        "most_disliked": disliked.most_common(10),
        "most_liked": liked.most_common(10),
        "custom_vibes": [{"key": v.key, "label": v.label, "uses": v.use_count, "status": v.status} for v in custom],
        "places": [
            {"place_id": s.place_id, "name": pois[s.place_id].name if s.place_id in pois else None, "category": pois[s.place_id].category if s.place_id in pois else None,
             "feedback": s.feedback_count, "confidence": s.confidence, "rating": s.bayes_rating, "synthetic_share": s.synthetic_share,
             "vibe_profile": dict(list(communities[s.place_id].as_dict()["vibe_profile"].items())[:6]) if s.place_id in communities else {}}
            for s in top_summaries
        ],
        "users": {"by_status": dict(buckets), "total": len(per_user)},
    }
    if user_id:
        result["user"] = user_vibes(user_id).as_dict()
    if destination_id:
        result["recommendation_changes"] = recommendation_changes(destination_id, user_id)
    return result


def recommendation_changes(destination_id: str, user_id: str | None) -> dict[str, Any]:
    """The same discovery ranking with and without feedback signals, to see what feedback changed."""
    geo = build_geo_context(user_location=None, active_destination={"id": destination_id})
    if geo.reference is None:
        return {"error": "unknown destination"}
    user = {"user_id": user_id}

    def run(use_feedback: bool) -> list[dict[str, Any]]:
        ranked = discover(DiscoveryRequest(reference=geo.reference, profile_name="discovery", kinds=set(taxonomy()["attraction_kinds"]), user=user, limit=10, allow_web=False, allow_osm=False, use_feedback=use_feedback)).candidates
        return [{"id": c.id, "name": c.name, "score": c.score, "vibe": c.scores.get("vibe"), "community": c.scores.get("community")} for c in ranked]

    before, after = run(False), run(True)
    position = {item["id"]: index for index, item in enumerate(before)}
    for index, item in enumerate(after):
        item["moved"] = (position[item["id"]] - index) if item["id"] in position else "new"
    return {"user_id": user_id, "without_feedback": before, "with_feedback": after}
