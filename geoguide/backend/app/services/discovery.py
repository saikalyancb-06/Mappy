"""Place discovery: the single candidate-generation pipeline.

reference point → PostGIS/spatial candidates → (live OSM when the area has no
data) → (SerpApi maps when internal coverage is thin) → normalise → dedupe →
hard filters → ranking. Used by /nearby, /ask, /plan and /now alike.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.logging import Trace
from app.core.rules import group_categories, load_rules, taxonomy
from app.geo.geo_context import ActiveReference
from app.geo.spatial import SpatialQuery, nearby
from app.ingestion.overpass import fetch_and_store
from app.models import Candidate
from app.ranking.ranker import RankRequest, rank
from app.retrieval.knowledge import retrieve
from app.search.aggregator import aggregate
from app.search.normalizer import candidate_from_web
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client


@dataclass
class DiscoveryRequest:
    reference: ActiveReference
    profile_name: str = "discovery"
    category: str | None = None
    group: str | None = None
    kinds: set[str] | None = None
    preferences: list[str] = field(default_factory=list)
    query_text: str | None = None
    user: dict[str, Any] = field(default_factory=dict)
    local_time: datetime | None = None
    time_sensitive: bool = False
    require_open: bool = False
    weather_signals: list[str] = field(default_factory=list)
    allow_web: bool = True
    allow_osm: bool = True
    limit: int | None = None
    user_point: tuple[float, float] | None = None
    categories: set[str] | None = None  # explicit category set (e.g. from plan wishes)
    exclude_categories: set[str] = field(default_factory=set)
    exclude_ids: set[str] = field(default_factory=set)
    avoid_tags: set[str] = field(default_factory=set)
    within_budget: bool = False
    constraints: Any = None  # app.query.constraints.Constraints
    use_feedback: bool = True
    include_tags: set[str] = field(default_factory=set)  # places with these tags match too (e.g. a "sunset spot" tagged sunset)
    travel_origin: tuple[float, float] | None = None


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    filtered_out: list[dict[str, Any]]
    counts: dict[str, int]
    provider_errors: list[dict[str, str]]
    web_used: bool


def _category_filter(request: DiscoveryRequest) -> tuple[set[str] | None, set[str] | None]:
    if request.categories:
        return set(request.categories), None
    if request.category:
        return {request.category}, None
    if request.group:
        return set(group_categories(request.group)), None
    return None, request.kinds


def _semantic_scores(request: DiscoveryRequest) -> dict[str, float]:
    """Semantic match of the question/preferences against POI descriptions & facts."""
    text = " ".join(filter(None, [request.query_text, " ".join(request.preferences)]))
    if not text.strip() or not request.reference.destination_id:
        return {}
    result = retrieve(text, destination_id=request.reference.destination_id, kinds=["poi_fact"], limit=30)
    scores: dict[str, float] = {}
    if not result.hits:
        return scores
    top = max(hit.scores.get("rrf", 0.0) for hit in result.hits) or 1.0
    for hit in result.hits:
        if hit.poi_id:
            scores[hit.poi_id] = max(scores.get(hit.poi_id, 0.0), hit.scores.get("rrf", 0.0) / top)
    return scores


def _web_query(request: DiscoveryRequest) -> str:
    if request.category:
        return taxonomy()["categories"][request.category].get("search_term") or request.category
    if request.group:
        return taxonomy()["groups"][request.group]["label"] + " places"
    if request.preferences:
        return " ".join(p.replace("_", " ") for p in request.preferences) + " places to visit"
    return "tourist attractions"


def apply_constraints(request: DiscoveryRequest) -> None:
    """Fold natural-language constraints into the request's filters and search radius."""
    c = request.constraints
    if c is None:
        return
    if not (request.category or request.group or request.categories) and (c.include_categories or c.include_groups):
        cats = set(c.include_categories)
        for group in c.include_groups:
            cats.update(group_categories(group))
        request.categories = cats
    request.include_tags = set(request.include_tags) | set(c.include_tags)
    request.exclude_categories = set(request.exclude_categories) | set(c.exclude_categories)
    request.avoid_tags = set(request.avoid_tags) | set(c.avoid_tags)
    request.preferences = list(dict.fromkeys([*request.preferences, *c.preferences]))
    if c.open_now:
        request.require_open = True
        request.time_sensitive = True
    if c.raining and "rain" not in request.weather_signals:
        request.weather_signals = [*request.weather_signals, "rain"]
    if c.max_travel_min:
        travel = load_rules("ranking")["travel"]
        speed = travel["speed_kmh"].get(c.travel_mode or travel["default_mode"], 4.5)
        reach = speed * c.max_travel_min / 60 / travel["detour_factor"]
        request.reference.radius_km = round(max(request.reference.radius_km, reach) if request.reference.semantic == "near_me" else min(request.reference.radius_km, max(reach, 0.5)), 2)


def matches_wish(candidate: Candidate, item: dict[str, Any]) -> bool:
    """A place matches a wish by category, or by tag when it is the right kind of place for it
    (a sunset-tagged lake is a sunset spot; a sunset-tagged bar is not)."""
    if candidate.category in item["categories"]:
        return True
    allowed = item.get("tag_categories")
    return bool(set(candidate.tags or []) & set(item["tags"])) and (not allowed or candidate.category in allowed)


def interleave_by_wish(ranked: list[Candidate], wishes: list[dict[str, Any]]) -> list[Candidate]:
    """Take turns between the things asked for, so one category can't crowd out the rest."""
    buckets: list[list[Candidate]] = [[] for _ in wishes]
    rest: list[Candidate] = []
    for candidate in ranked:
        index = next((i for i, item in enumerate(wishes) if matches_wish(candidate, item)), None)
        (buckets[index] if index is not None else rest).append(candidate)
    out: list[Candidate] = []
    while any(buckets):
        for bucket in buckets:
            if bucket:
                out.append(bucket.pop(0))
    return out + rest


def discover(request: DiscoveryRequest, trace: Trace | None = None, web: SerpApiClient | None = None) -> DiscoveryResult:
    web = web or default_client
    config = load_rules("ranking")
    apply_constraints(request)
    reference = request.reference
    categories, kinds = _category_filter(request)
    wished: set[str] | None = None
    if categories and request.include_tags:
        # Match on category OR tag: fetch every attraction and filter after merging sources.
        wished, categories, kinds = set(categories), None, request.kinds or set(load_rules("taxonomy")["attraction_kinds"])
    errors: list[dict[str, str]] = []
    counts = {"database": 0, "osm_fetched": 0, "web": 0, "duplicates_merged": 0}

    spatial_query = SpatialQuery(lat=reference.lat, lon=reference.lon, radius_km=reference.radius_km, categories=categories, kinds=kinds)
    internal = nearby(spatial_query)
    counts["database"] = len(internal)

    # Areas with no curated pack: pull OpenStreetMap once, persist, re-query.
    if request.allow_osm and not reference.destination_id and len(internal) < config["min_results_before_web"]:
        fetched, error = fetch_and_store(reference.lat, reference.lon, min(max(reference.radius_km, 1.0), 10.0))
        counts["osm_fetched"] = fetched
        if error:
            errors.append(error)
        elif fetched:
            internal = nearby(spatial_query)
            counts["database"] = len(internal)

    web_candidates: list[Candidate] = []
    web_used = False
    if request.allow_web and len(internal) < config["min_results_before_web"]:
        if web.configured:
            web_used = True
            zoom = 15 if reference.radius_km <= 2 else 13 if reference.radius_km <= 8 else 12
            try:
                response = web.search(_web_query(request), engine="google_maps", lat=reference.lat, lon=reference.lon, zoom=zoom, limit=20)
                web_candidates = [c for c in (candidate_from_web(r, response.retrieved_at, reference=(reference.lat, reference.lon), category_hint=request.category) for r in response.results) if c]
                counts["web"] = len(web_candidates)
                if trace:
                    trace.step("web_places", query=response.query, results=len(response.results), candidates=len(web_candidates), cached=response.cached)
            except SearchProviderError as exc:
                errors.append(exc.as_dict())
        else:
            errors.append({"source": "serpapi", "code": "web_search_unavailable", "message": "Web search is not configured; showing stored places only."})

    merged, duplicates = aggregate([internal, web_candidates])
    if wished is not None:
        wishes = (request.constraints.wishes if request.constraints is not None else None) or []
        merged = [c for c in merged if c.category in wished or any(matches_wish(c, item) for item in wishes)]
    counts["duplicates_merged"] = duplicates
    semantic = _semantic_scores(request) if (request.preferences or request.query_text) else {}
    rank_request = RankRequest(
        profile_name=request.profile_name,
        reference=(reference.lat, reference.lon),
        reference_label=reference.distance_label,
        radius_km=reference.radius_km,
        categories=categories,
        kinds=kinds,
        requested_category=request.category,
        requested_group=request.group,
        preferences=request.preferences,
        query_text=request.query_text,
        user=request.user,
        weather_signals=request.weather_signals,
        local_time=request.local_time,
        require_open=request.require_open,
        time_sensitive=request.time_sensitive,
        semantic_scores=semantic,
        user_point=request.user_point,
        exclude_categories=set(request.exclude_categories),
        exclude_ids=set(request.exclude_ids),
        avoid_tags=set(request.avoid_tags),
        within_budget=request.within_budget,
        constraints=request.constraints,
        travel_origin=request.travel_origin,
        use_feedback=request.use_feedback,
    )
    result = rank(merged, rank_request)
    ranked = result.ranked
    limit = request.limit or config["max_results"]
    if request.constraints is not None and request.constraints.limit:
        # Decision mode: only a few options, and never low-confidence ones.
        limit = request.constraints.limit
        confident = [c for c in ranked if c.confidence_detail.get("label") != "low"]
        ranked = confident or ranked
    wishes = (request.constraints.wishes if request.constraints is not None else None) or []
    if len(wishes) > 1:
        ranked = interleave_by_wish(ranked, wishes)
    result.ranked = ranked
    if trace:
        trace.step(
            "discovery",
            reference={"origin": reference.origin, "label": reference.label, "lat": reference.lat, "lon": reference.lon, "radius_km": reference.radius_km},
            hard_filters={"categories": sorted(categories) if categories else None, "kinds": sorted(kinds) if kinds else None, "require_open": request.require_open},
            counts=counts,
            filtered_out=result.filtered_out[:20],
            ranking=[{"id": c.id, "name": c.name, "score": c.score, "scores": c.scores, "distance_km": c.distance_km, "sources": [s.source_type for s in c.sources]} for c in result.ranked[:limit]],
        )
    return DiscoveryResult(result.ranked[:limit], result.filtered_out, counts, errors, web_used)
