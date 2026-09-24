"""Vibe and aspect evidence from provider reviews.

Review snippets are read with the same text analysis as GeoGuide feedback. Only
what the text actually says counts: a vibe needs a matching phrase, and a place's
category never creates one. Counts are stored per place in
``place_review_signals`` and blended into the place vibe profile at a lower
weight than GeoGuide feedback (``vibes.json → aggregation.review_weight``).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete

from app.db.models import PlaceReviewSignal
from app.db.session import SessionLocal
from app.feedback.aggregate import recompute_place
from app.feedback.nlp import analyse
from app.places.providers.base import PlaceReview


def store_review_signals(poi_id: str, reviews: list[PlaceReview], source: str = "google_maps_reviews") -> dict[str, int]:
    texts = [r for r in reviews if r.text and len(r.text) >= 12]
    if not texts:
        return {}
    vibes: Counter[str] = Counter()
    negatives: Counter[str] = Counter()
    positives: Counter[str] = Counter()
    for review in texts:
        signals = analyse(review.text, int(round(review.rating)) if review.rating else None)
        vibes.update(set(signals.vibes))
        negatives.update(set(signals.disliked_aspects))
        positives.update(set(signals.liked_aspects))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [("vibe", key, 1.0, count) for key, count in vibes.items()] + [("aspect", key, -1.0, count) for key, count in negatives.items()] + [("aspect", key, 1.0, count) for key, count in positives.items()]
    with SessionLocal() as db:
        db.execute(delete(PlaceReviewSignal).where(PlaceReviewSignal.poi_id == poi_id, PlaceReviewSignal.source == source))
        for kind, key, polarity, count in rows:
            db.add(PlaceReviewSignal(id=f"{poi_id}:{source}:{kind}:{key}:{int(polarity)}", poi_id=poi_id, kind=kind, key=key, polarity=polarity, evidence_count=count, reviews_read=len(texts), source=source, updated_at=now))
        db.commit()
    recompute_place(poi_id)
    return {"reviews": len(texts), "vibes": len(vibes), "negative_aspects": len(negatives)}
