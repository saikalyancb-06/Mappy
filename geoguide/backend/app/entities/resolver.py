"""Data-driven entity resolution.

Resolves a mention such as "<business> in <neighbourhood>" to one canonical
place using name similarity, aliases, branch/suffix analysis, locality,
category compatibility, distance and cross-source agreement. When the evidence
is insufficient the result is ``ambiguous`` or ``not_found`` — never a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import false, or_, select

from app.core.logging import Trace
from app.core.rules import load_rules
from app.core.text import distinctive_tokens, name_similarity, normalize, tokens
from app.db.models import EntityAlias, Poi
from app.db.session import SessionLocal
from app.geo.distance import haversine_km
from app.geo.geocoding import ResolvedPlace, resolve_place
from app.models import Candidate, candidate_from_poi
from app.search.aggregator import aggregate
from app.search.normalizer import candidate_from_web
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float
    signals: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.candidate.id, "name": self.candidate.name, "address": self.candidate.address, "category": self.candidate.category, "score": round(self.score, 3), "signals": {k: round(v, 3) for k, v in self.signals.items()}, "sources": [s.source_type for s in self.candidate.sources]}


@dataclass
class Resolution:
    status: str  # resolved | ambiguous | not_found
    mention: str
    entity: Candidate | None = None
    confidence: float = 0.0
    alternatives: list[ScoredCandidate] = field(default_factory=list)
    locality: ResolvedPlace | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mention": self.mention,
            "entity_id": self.entity.id if self.entity else None,
            "entity_name": self.entity.name if self.entity else None,
            "confidence": round(self.confidence, 3),
            "alternatives": [alt.as_dict() for alt in self.alternatives[:5]],
            "locality": self.locality.as_dict() if self.locality else None,
            "notes": self.notes,
        }


def _database_candidates(mention: str, reference: tuple[float, float] | None) -> list[Candidate]:
    words = [w for w in tokens(mention) if len(w) > 2] or tokens(mention)
    if not words:
        return []
    with SessionLocal() as db:
        name_clause = or_(*[Poi.normalized_name.contains(word) for word in words])
        alias_ids = {row.entity_id for row in db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "poi", or_(*[EntityAlias.normalized.contains(word) for word in words]))).all()}
        pois = db.scalars(select(Poi).where(or_(name_clause, Poi.id.in_(list(alias_ids)) if alias_ids else false()))).all()
        aliases: dict[str, list[str]] = {}
        for row in db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "poi", EntityAlias.entity_id.in_([p.id for p in pois]))).all():
            aliases.setdefault(row.entity_id, []).append(row.alias)
    out = []
    for poi in pois:
        distance = haversine_km(reference[0], reference[1], poi.lat, poi.lon) if reference else None
        candidate = candidate_from_poi(poi, distance)
        candidate.facts = [{"alias": alias} for alias in aliases.get(poi.id, [])]
        out.append(candidate)
    return out


def _web_candidates(mention: str, locality_text: str | None, point: tuple[float, float] | None, web: SerpApiClient, trace: Trace | None) -> list[Candidate]:
    if not web.configured:
        return []
    query = f"{mention} {locality_text}" if locality_text else mention
    try:
        response = web.search(query, engine="google_maps", lat=point[0] if point else None, lon=point[1] if point else None, zoom=14, limit=10)
    except SearchProviderError as exc:
        if trace:
            trace.error("serpapi", exc.code, exc.message)
        return []
    candidates = [c for c in (candidate_from_web(r, response.retrieved_at, reference=point) for r in response.results) if c]
    if trace:
        trace.step("entity_web_candidates", query=query, count=len(candidates), cached=response.cached)
    return candidates


def _acronym_explained(mention_tokens: set[str], name: str) -> set[str]:
    """Candidate-name tokens accounted for by an acronym in the mention ("SLV" → Sri Lakshmi Venkateshwara)."""
    words = tokens(name)
    explained: set[str] = set()
    for acronym in (t for t in mention_tokens if 2 <= len(t) <= 6):
        for start in range(len(words)):
            span = words[start : start + len(acronym)]
            if len(span) == len(acronym) and "".join(word[0] for word in span) == acronym:
                explained.update(span)
    return explained


def _compatible(hint: str | None, category: str | None) -> float:
    if not hint:
        return 0.5
    if not category:
        return 0.4
    if hint == category:
        return 1.0
    compat = load_rules("ranking")["entity_resolution"]["category_compatibility"].get(hint, [])
    return 0.8 if category in compat else 0.0


def score_candidate(candidate: Candidate, mention: str, locality_text: str | None, locality_point: tuple[float, float] | None, category_hint: str | None, reference: tuple[float, float] | None) -> tuple[float, dict[str, float]]:
    rules = load_rules("ranking")["entity_resolution"]
    weights = rules["weights"]
    alias_names = [fact["alias"] for fact in candidate.facts if isinstance(fact, dict) and fact.get("alias")]
    name_score = max([name_similarity(mention, candidate.name), *[name_similarity(mention, alias) for alias in alias_names]])

    # Branch / suffix analysis: distinctive words in the candidate name that the user did not say
    # ("<name> Jayanagar" vs "<name>") indicate a different branch unless the locality explains them.
    mention_tokens = distinctive_tokens(mention) | distinctive_tokens(locality_text)
    extra = distinctive_tokens(candidate.name) - mention_tokens - _acronym_explained(set(tokens(mention)), candidate.name)
    if alias_names and any(distinctive_tokens(alias) <= mention_tokens and distinctive_tokens(alias) for alias in alias_names):
        extra = set()
    branch_penalty = min(rules["branch_penalty_max"], rules["branch_penalty_per_token"] * len(extra))

    if locality_text:
        haystack = normalize(" ".join(filter(None, [candidate.address, candidate.neighborhood, candidate.name])))
        locality_tokens = distinctive_tokens(locality_text)
        text_match = len([t for t in locality_tokens if t in haystack.split()]) / len(locality_tokens) if locality_tokens else 0.0
        near_match = 0.0
        if locality_point and candidate.lat is not None:
            near_match = math.exp(-haversine_km(locality_point[0], locality_point[1], candidate.lat, candidate.lon) / 1.5)
        locality = max(text_match, near_match)
    else:
        locality = 0.5

    # A named locality defines "where"; the traveller's own position is only used when no locality was given.
    anchor = locality_point or (reference if not locality_text else None)
    distance = math.exp(-haversine_km(anchor[0], anchor[1], candidate.lat, candidate.lon) / rules["distance_scale_km"]) if anchor and candidate.lat is not None else 0.5
    agreement = min(1.0, (len({s.source_type for s in candidate.sources}) - 1) / 2) if candidate.sources else 0.0
    category = _compatible(category_hint, candidate.category)
    signals = {"name": name_score, "locality": locality, "category": category, "distance": distance, "source_agreement": agreement, "branch_penalty": -branch_penalty}
    score = sum(weights[key] * signals[key] for key in weights) - branch_penalty
    return max(0.0, min(1.0, score)), signals


def resolve_entity(
    mention: str,
    *,
    locality_hint: str | None = None,
    category_hint: str | None = None,
    reference: tuple[float, float] | None = None,
    allow_web: bool = True,
    prefer_live: bool = False,
    web: SerpApiClient | None = None,
    trace: Trace | None = None,
) -> Resolution:
    rules = load_rules("ranking")["entity_resolution"]
    web = web or default_client
    resolution = Resolution(status="not_found", mention=mention)

    locality_point = None
    if locality_hint:
        locality, _ = resolve_place(locality_hint, near=reference)
        if locality:
            resolution.locality = locality
            locality_point = (locality.lat, locality.lon)
        else:
            resolution.notes.append(f"Locality “{locality_hint}” could not be located; matching on text only.")
    anchor = locality_point or reference

    database = _database_candidates(mention, anchor)
    best_db = max((name_similarity(mention, c.name) for c in database), default=0.0)
    web_candidates: list[Candidate] = []
    if allow_web and (prefer_live or best_db < 0.85 or locality_hint):
        web_candidates = _web_candidates(mention, locality_hint or (resolution.locality.city if resolution.locality else None), anchor, web, trace)
    candidates, duplicates = aggregate([database, web_candidates])

    scored = []
    for candidate in candidates:
        score, signals = score_candidate(candidate, mention, locality_hint, locality_point, category_hint, reference)
        if score >= rules["min_candidate_score"]:
            scored.append(ScoredCandidate(candidate, score, signals))
    scored.sort(key=lambda item: item.score, reverse=True)
    resolution.alternatives = scored[:6]
    if trace:
        trace.step("entity_resolution", mention=mention, locality=locality_hint, database_candidates=len(database), web_candidates=len(web_candidates), merged_duplicates=duplicates, scored=[s.as_dict() for s in scored[:5]])
    if not scored:
        return resolution
    top = scored[0]
    second = scored[1].score if len(scored) > 1 else 0.0
    if top.score >= rules["resolve_threshold"] and top.score - second >= rules["ambiguity_margin"]:
        resolution.status = "resolved"
        resolution.entity = top.candidate
        resolution.confidence = top.score
    else:
        resolution.status = "ambiguous"
        resolution.confidence = top.score
    return resolution
