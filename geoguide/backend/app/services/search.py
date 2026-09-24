"""Search anything the traveller has heard of: a place, a hotel, a destination or a category.

Order: stored places/hotels by name and alias (fuzzy), curated/dataset
destinations, category words ("temples") around the reference, then live
map search when stored matches are weak. Results are canonical candidates
with cost, confidence and provenance like everywhere else.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import Trace
from app.core.rules import taxonomy
from difflib import SequenceMatcher

from app.core.text import distinctive_tokens, name_similarity, normalize, tokens
from app.entities.resolver import _database_candidates
from sqlalchemy import select

from app.db.models import EntityAlias, Poi
from app.db.session import SessionLocal
from app.geo.distance import haversine_km
from app.geo.spatial import get_pois
from app.geo.geo_context import ActiveReference, all_destinations
from app.geo.geocoding import search_destinations
from app.models import Candidate
from app.ranking.ranker import RankRequest, rank
from app.search.aggregator import aggregate
from app.places.live import persist_live_results
from app.search.normalizer import candidate_from_web
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client
from app.services.discovery import DiscoveryRequest, discover

MIN_MATCH = 0.55
STRONG_MATCH = 0.8


def _covers(query: str, name: str) -> bool:
    """Every distinctive word the traveller typed must appear (allowing small typos) in the name."""
    wanted = distinctive_tokens(query) or set(tokens(query))
    have = set(tokens(name))
    def close(w: str, h: str) -> bool:
        # Short words ("rok") must also share the first letter so typos don't match unrelated names.
        return len(w) >= 3 and (len(w) > 3 or w[0] == h[0]) and SequenceMatcher(None, w, h).ratio() >= 0.8

    return all(any(w == h or close(w, h) for h in have) for w in wanted)


def _fuzzy_candidates(query: str, anchor: tuple[float, float] | None, limit: int = 20) -> list[Candidate]:
    """Typo-tolerant fallback over every stored name and alias (fine at pack/dataset scale)."""
    with SessionLocal() as db:
        names = db.execute(select(Poi.id, Poi.name)).all() + db.execute(select(EntityAlias.entity_id, EntityAlias.alias).where(EntityAlias.entity_type == "poi")).all()
    hits = sorted({pid for pid, name in names if _covers(query, name)})[:limit]
    return get_pois(hits, anchor)


def _category_for(query: str) -> str | None:
    norm = normalize(query)
    for category_id, entry in taxonomy()["categories"].items():
        if norm in {normalize(s) for s in entry["synonyms"]}:
            return category_id
    return None


def search(query: str, *, reference: ActiveReference | None, user_point: tuple[float, float] | None, profile: dict[str, Any], kind: str | None = None, limit: int = 12, trace: Trace | None = None, web: SerpApiClient | None = None) -> dict[str, Any]:
    web = web or default_client
    errors: list[dict[str, str]] = []
    anchor = (reference.lat, reference.lon) if reference else user_point
    destinations = [d for d in search_destinations(query, limit=5) if name_similarity(query, d["name"]) >= 0.7] if kind in (None, "destination") else []

    category = _category_for(query)
    category_results: list[Candidate] = []
    if category and reference:
        category_results = discover(DiscoveryRequest(reference=reference, category=category, user=profile, user_point=user_point, allow_web=True, limit=limit), trace=trace).candidates

    stored = [] if category else _database_candidates(query, anchor)
    if not category and not any(_covers(query, c.name) for c in stored):
        stored = stored + _fuzzy_candidates(query, anchor)
    matches: dict[str, float] = {}
    for candidate in stored:
        aliases = [f["alias"] for f in candidate.facts if isinstance(f, dict) and f.get("alias")]
        names = [candidate.name, *aliases]
        # Covering every typed word (typo-tolerant) is itself strong evidence of a match.
        matches[candidate.id] = max(0.75, max(name_similarity(query, n) for n in names)) if any(_covers(query, n) for n in names) else 0.0
    stored = [c for c in stored if matches[c.id] >= MIN_MATCH]

    live: list[Candidate] = []
    if not category and web.configured and sum(1 for c in stored if matches[c.id] >= STRONG_MATCH) < 2:
        try:
            response = web.search(query, engine="google_maps", lat=anchor[0] if anchor else None, lon=anchor[1] if anchor else None, zoom=13, limit=10)
            live = [c for c in (candidate_from_web(r, response.retrieved_at, reference=anchor) for r in response.results) if c]
            # An unknown place found live is stored when it lies inside the registered city.
            persist_live_results(reference.destination_id if reference else None, response.results)
            for candidate in live:
                matches[candidate.id] = max(name_similarity(query, candidate.name), 0.6)
            if trace:
                trace.step("search_live", query=query, results=len(live), cached=response.cached)
        except SearchProviderError as exc:
            errors.append(exc.as_dict())
    elif not category and not web.configured and not stored:
        errors.append({"source": "serpapi", "code": "web_search_unavailable", "message": "Only stored places were searched (web search is not configured)."})

    merged, _ = aggregate([stored, live])
    for candidate in merged:
        matches.setdefault(candidate.id, max((matches.get(s.source_id or "", 0) for s in candidate.sources), default=0.6))
    ranked = rank(merged, RankRequest(profile_name="lookup", reference=anchor, reference_label="you" if user_point and anchor == user_point else (reference.distance_label if reference else None), user=profile, user_point=user_point)).ranked
    if kind == "stay":
        ranked = [c for c in ranked if c.kind == "stay"]
    elif kind == "place":
        ranked = [c for c in ranked if c.kind != "stay"]
    ranked.sort(key=lambda c: (-round(matches.get(c.id, 0), 2), c.distance_km if c.distance_km is not None else 1e9))
    for candidate in ranked:
        candidate.scores["match"] = round(matches.get(candidate.id, 0), 3)
        if user_point and candidate.lat is not None and not any(r.endswith(" from you") for r in candidate.reasons):
            candidate.reasons = [f"{haversine_km(user_point[0], user_point[1], candidate.lat, candidate.lon):.1f} km from you", *candidate.reasons]
    if trace:
        trace.step("search", query=query, category=category, stored=len(stored), live=len(live), destinations=len(destinations))
    places = (category_results or ranked)[:limit]
    names = {d.id: d.name for d in all_destinations()}
    for candidate in places:
        candidate.facts = [f for f in candidate.facts if not (isinstance(f, dict) and "alias" in f)]
        candidate.destination_name = names.get(candidate.destination_id or "")
    return {"query": query, "category": category, "places": places, "destinations": destinations, "provider_errors": errors}
