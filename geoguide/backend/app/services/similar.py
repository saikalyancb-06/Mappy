"""'More like this': places similar to one the traveller is looking at.

Similarity is explainable and cheap (no embeddings): same category, same kind of
place (taxonomy group), shared community vibes, quality, and closeness. The
recommendation policy applies, so closed or excluded places never appear.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.rules import category_group, taxonomy
from app.db.models import Poi
from app.db.session import SessionLocal
from app.feedback.signals import place_communities
from app.geo.distance import haversine_km
from app.models import Candidate, candidate_from_poi
from app.policy.recommendation import context_from_profile, exclusion_reason

NEARBY_KM = 15.0


def _groups(category: str | None) -> set[str]:
    return {g for g, entry in taxonomy()["groups"].items() if category and category in entry.get("categories", [])}


def similar_places(poi_id: str, *, profile: dict[str, Any] | None = None, limit: int = 6) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        base = db.get(Poi, poi_id)
        if base is None:
            return []
        query = select(Poi).where(Poi.id != poi_id, Poi.kind == base.kind)
        query = query.where(Poi.destination_id == base.destination_id) if base.destination_id else query.where(Poi.lat.between(base.lat - 0.15, base.lat + 0.15), Poi.lon.between(base.lon - 0.15, base.lon + 0.15))
        rows = db.scalars(query).all()
    if not rows:
        return []
    context = context_from_profile(profile)
    communities = place_communities([base, *rows])
    base_vibes = set(communities[base.id].top_vibes(4, 0.3)) if base.id in communities else set()
    base_groups = _groups(base.category)
    scored: list[tuple[float, Candidate, list[str]]] = []
    for row in rows:
        distance = haversine_km(base.lat, base.lon, row.lat, row.lon)
        if distance > NEARBY_KM:
            continue
        candidate = candidate_from_poi(row, distance)
        if exclusion_reason(candidate, context):
            continue
        reasons = []
        score = 0.0
        if row.category and row.category == base.category:
            score += 0.4
            reasons.append(f"Also a {(taxonomy()['categories'].get(row.category, {}).get('label') or row.category).lower()}")
        elif base_groups & _groups(row.category):
            score += 0.2
            reasons.append(f"Same kind of place ({category_group(row.category) or 'similar'})")
        vibes = set(communities[row.id].top_vibes(4, 0.3)) if row.id in communities else set()
        shared = base_vibes & vibes
        if shared:
            score += 0.3 * len(shared) / max(1, len(base_vibes | vibes))
            reasons.append("Similar vibe: " + ", ".join(sorted(shared)).replace("_", " "))
        if row.rating:
            score += 0.2 * min(1.0, row.rating / 5.0)
        score += 0.1 * max(0.0, 1.0 - distance / NEARBY_KM)
        if score >= 0.35:
            scored.append((score, candidate, reasons))
    scored.sort(key=lambda item: -item[0])
    out = []
    for score, candidate, reasons in scored[:limit]:
        data = candidate.as_dict()
        data["similarity"] = round(score, 3)
        data["similar_because"] = reasons
        out.append(data)
    return out
