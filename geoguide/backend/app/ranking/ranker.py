"""Candidate ranking.

Hard filters decide what is eligible (geography, entity type, validity,
accessibility requirements, permanently closed). Soft signals, weighted by a
configurable profile per intent, decide order. Every candidate keeps its score
breakdown and data-backed reasons so "why this place?" is answerable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.rules import categories_for_interest, category_group, group_categories, load_rules, taxonomy
from app.core.text import tokens
from app.geo import opening_hours
from app.geo.distance import haversine_km, valid_coordinates
from app.models import Candidate
from app.ranking.confidence import assess, bars
from app.services.cost import cost_for_user, to_decimal


@dataclass
class RankRequest:
    profile_name: str = "discovery"
    reference: tuple[float, float] | None = None
    reference_label: str | None = None
    radius_km: float | None = None
    categories: set[str] | None = None  # hard filter
    kinds: set[str] | None = None  # hard filter
    requested_category: str | None = None
    requested_group: str | None = None
    preferences: list[str] = field(default_factory=list)
    query_text: str | None = None
    user: dict[str, Any] = field(default_factory=dict)  # interests, budget, pace, walking, accessibility, likes, dislikes
    weather_signals: list[str] = field(default_factory=list)
    local_time: datetime | None = None
    require_open: bool = False
    time_sensitive: bool = False
    semantic_scores: dict[str, float] = field(default_factory=dict)  # poi_id -> 0..1
    user_point: tuple[float, float] | None = None
    exclude_categories: set[str] = field(default_factory=set)  # hard: "no museums"
    exclude_ids: set[str] = field(default_factory=set)
    avoid_tags: set[str] = field(default_factory=set)  # soft: "not crowded"
    within_budget: bool = False  # hard: only places that fit the traveller's budget
    constraints: Any = None  # app.query.constraints.Constraints
    travel_origin: tuple[float, float] | None = None  # where travel time is measured from


@dataclass
class RankResult:
    ranked: list[Candidate]
    filtered_out: list[dict[str, Any]]


def _hard_filter(candidate: Candidate, request: RankRequest) -> str | None:
    if not valid_coordinates(candidate.lat, candidate.lon):
        return "invalid_coordinates"
    if request.reference and request.radius_km is not None:
        distance = haversine_km(request.reference[0], request.reference[1], candidate.lat, candidate.lon)
        candidate.distance_km = round(distance, 3)
        if distance > request.radius_km:
            return "outside_radius"
    if request.categories and candidate.category not in request.categories:
        return "category_mismatch"
    if candidate.category in request.exclude_categories:
        return "excluded_category"
    if candidate.id in request.exclude_ids:
        return "excluded_by_traveller"
    if request.within_budget and candidate.cost_for_user.get("fits_budget") is False:
        return "over_budget"
    c = request.constraints
    if c is not None:
        if c.max_travel_min and candidate.travel_min is not None and candidate.travel_min > c.max_travel_min:
            return "too_far_to_travel"
        if c.min_rating and candidate.rating is not None and candidate.rating < c.min_rating:
            return "rating_below_minimum"
        if c.max_price_level and candidate.price_level is not None and candidate.price_level > c.max_price_level:
            return "above_price_level"
        amount = to_decimal(candidate.cost_for_user.get("amount"))
        if c.max_cost and amount is not None and (not c.cost_currency or not candidate.cost_for_user.get("currency") or c.cost_currency == candidate.cost_for_user.get("currency")) and amount > to_decimal(c.max_cost):
            return "above_max_cost"
        if c.crowd == "low" and set(candidate.tags) & set(load_rules("ranking")["crowd"]["crowded_tags"]):
            return "crowded"
        mode_rules = load_rules("ranking")["ranking_modes"].get(c.ranking_mode or "")
        if c.ranking_mode == "hidden_gems" and mode_rules:
            if candidate.popularity_score is not None and candidate.popularity_score > mode_rules["max_popularity"]:
                return "too_popular_for_hidden_gem"
            if set(candidate.tags) & set(mode_rules["exclude_tags"]):
                return "too_popular_for_hidden_gem"
        if c.ranking_mode == "local" and mode_rules and not set(candidate.tags) & set(mode_rules["require_any_tag"]):
            return "not_a_local_favourite"
    if request.kinds and candidate.kind not in request.kinds:
        return "kind_mismatch"
    if candidate.open_detail.get("permanently_closed"):
        return "permanently_closed"
    needs_step_free = "step_free" in (request.user.get("accessibility") or []) or "step_free" in request.preferences
    if needs_step_free and candidate.step_free is False:
        return "not_step_free"
    if request.require_open and candidate.open_status == "closed":
        return "closed_now"
    return None


def _quality(candidate: Candidate) -> float:
    rules = load_rules("ranking")["quality"]
    tag_bonus = sum(rules["tag_bonus"].get(tag, 0.0) for tag in candidate.tags)
    if candidate.rating is None:
        base = candidate.popularity_score / 100 if candidate.popularity_score is not None else rules["unrated_score"]
        return min(1.0, base + tag_bonus)
    votes = float(candidate.review_count or 0)
    prior_votes = float(rules["prior_votes"])
    bayes = (votes / (votes + prior_votes)) * candidate.rating + (prior_votes / (votes + prior_votes)) * rules["prior_rating"]
    return max(0.0, min(1.0, (bayes - 1.0) / 4.0 + tag_bonus))


def _relevance(candidate: Candidate, request: RankRequest) -> tuple[float, list[str]]:
    rules = load_rules("ranking")["relevance"]
    reasons = []
    if request.requested_category:
        if candidate.category == request.requested_category:
            base = rules["exact_category"]
        elif category_group(candidate.category) and category_group(candidate.category) == category_group(request.requested_category):
            base = rules["same_group"]
        else:
            base = 0.2
    elif request.requested_group:
        base = rules["exact_category"] if candidate.category in set(group_categories(request.requested_group)) else 0.3
    else:
        base = rules["no_category_requested"]
    preference_score = 0.0
    if request.preferences:
        matched = [p for p in request.preferences if p in candidate.tags or (p == "budget" and (candidate.price_level or 0) <= 1) or (p == "step_free" and candidate.step_free) or (p == "shade" and candidate.indoor)]
        preference_score = len(matched) / len(request.preferences)
        if matched:
            reasons.append("Fits: " + ", ".join(m.replace("_", " ") for m in matched))
    semantic = request.semantic_scores.get(candidate.id, 0.0)
    text_match = 0.0
    if request.query_text:
        query_terms = {t for t in tokens(request.query_text) if len(t) > 3}
        haystack = set(tokens(f"{candidate.name} {candidate.description or ''} {' '.join(candidate.tags)}"))
        text_match = len(query_terms & haystack) / len(query_terms) if query_terms else 0.0
    score = base * (1 - rules["preference_tag_weight"] - rules["semantic_weight"]) + preference_score * rules["preference_tag_weight"] + max(semantic, text_match) * rules["semantic_weight"]
    if not request.preferences:
        score = base * (1 - rules["semantic_weight"]) + max(semantic, text_match) * rules["semantic_weight"]
    return max(0.0, min(1.0, score)), reasons


def _preference(candidate: Candidate, request: RankRequest) -> tuple[float, list[str]]:
    user = request.user or {}
    reasons: list[str] = []
    score, parts = 0.0, 0
    interests = user.get("interests") or {}
    if isinstance(interests, list):
        interests = {item: 1 for item in interests}
    if interests:
        parts += 1
        best = 0.0
        matched = None
        top = max(float(v) for v in interests.values()) or 1.0
        for interest, weight in interests.items():
            if candidate.category in categories_for_interest(interest) or interest in candidate.tags:
                if float(weight) / top > best:
                    best, matched = float(weight) / top, interest
        score += best
        if matched:
            label = taxonomy()["groups"].get(matched, {}).get("label") or matched.replace("_", " ").title()
            reasons.append(f"Matches your interest in {label}")
    avoided = request.avoid_tags & set(candidate.tags)
    if avoided:
        parts += 1
        reasons.append("Tagged " + ", ".join(sorted(avoided)).replace("_", " ") + " (you asked to avoid)")
    likes, dislikes = set(user.get("likes") or []), set(user.get("dislikes") or [])
    if candidate.id in dislikes or candidate.category in dislikes:
        return 0.0, ["You marked this type as a dislike"]
    if candidate.id in likes or candidate.category in likes:
        score += 1.0
        parts += 1
        reasons.append("Similar to places you liked")
    fit = candidate.cost_for_user.get("fits_budget")
    if fit is not None:
        parts += 1
        score += 1.0 if fit else 0.1
        reasons.append(("Fits your budget" if fit else "Over your budget") + f" ({candidate.cost_for_user['note']})")
    budget = user.get("budget")
    if fit is None and budget and candidate.price_level is not None:
        parts += 1
        limit = {"low": 1, "moderate": 2, "high": 4}.get(budget, 2)
        if candidate.price_level <= limit:
            score += 1.0
            if budget == "low" and candidate.price_level == 0:
                reasons.append("Free to visit")
    walking = user.get("walking") or {"relaxed": "low", "packed": "high"}.get(user.get("pace") or "", None)
    if walking and candidate.walking_effort:
        parts += 1
        tolerance = {"low": 0, "moderate": 1, "high": 2}.get(walking, 1)
        effort = {"low": 0, "moderate": 1, "high": 2}.get(candidate.walking_effort, 1)
        score += 1.0 if effort <= tolerance else 0.2
        if effort > tolerance:
            reasons.append(f"More walking than you prefer ({candidate.walking_effort} effort)")
    needs_step_free = "step_free" in (user.get("accessibility") or [])
    if needs_step_free:
        parts += 1
        if candidate.step_free:
            score += 1.0
            reasons.append("Step-free access")
        elif candidate.step_free is None:
            score += 0.4
            reasons.append("Step-free access not verified")
    return (score / parts if parts else 0.5), reasons


def _weather(candidate: Candidate, request: RankRequest) -> tuple[float, list[str]]:
    rules = load_rules("ranking")["weather"]
    if not request.weather_signals:
        return rules["neutral"], []
    sheltered = candidate.indoor or "shade" in candidate.tags
    if sheltered:
        signal = "heat" if "heat" in request.weather_signals else "rain"
        return rules["indoor_bonus"], [f"Sheltered — good for today's {signal}"]
    if candidate.indoor is False and candidate.walking_effort == "high":
        return rules["exposed_penalty"], []
    return rules["neutral"], []


def _open(candidate: Candidate, request: RankRequest) -> tuple[float, list[str]]:
    if request.local_time and candidate.opening_hours:
        status = opening_hours.status_at(candidate.opening_hours, request.local_time)
        candidate.open_status = status["status"]
        candidate.open_detail = {**candidate.open_detail, **{k: v for k, v in status.items() if k != "status"}}
    if not request.time_sensitive:
        return 0.5, []
    if candidate.open_status == "open":
        closes = candidate.open_detail.get("closes_at")
        return 1.0, [f"Open now{f' until {closes}' if closes else ''}"]
    if candidate.open_status == "closed":
        opens = candidate.open_detail.get("opens_at")
        return 0.0, [f"Closed now{f' (opens {opens})' if opens else ''}"]
    return 0.5, []


def _format_distance(km: float) -> str:
    return f"{int(round(km * 1000, -1))} m" if km < 1 else f"{km:.1f} km"


def rank(candidates: list[Candidate], request: RankRequest) -> RankResult:
    config = load_rules("ranking")
    weights = config["profiles"].get(request.profile_name) or config["profiles"]["discovery"]
    source_conf = config["source_confidence"]
    ranked: list[Candidate] = []
    filtered: list[dict[str, Any]] = []
    travel = config["travel"]
    constraints = request.constraints
    mode = (constraints.travel_mode if constraints and constraints.travel_mode else None) or request.user.get("travel_mode") or None
    origin = request.travel_origin or request.user_point or request.reference
    for candidate in candidates:
        candidate.cost_for_user = cost_for_user(candidate, request.user or {})
        if origin and candidate.lat is not None:
            speed = travel["speed_kmh"].get(mode or travel["default_mode"], 4.5)
            km = haversine_km(origin[0], origin[1], candidate.lat, candidate.lon) * travel["detour_factor"]
            candidate.travel_min = int(round(km / speed * 60 + (travel["transit_wait_min"] if mode == "transit" else 0)))
            candidate.travel_mode = mode or travel["default_mode"]
        open_score, open_reasons = _open(candidate, request)
        reason = _hard_filter(candidate, request)
        if reason:
            filtered.append({"id": candidate.id, "name": candidate.name, "reason": reason})
            continue
        relevance, relevance_reasons = _relevance(candidate, request)
        preference, preference_reasons = _preference(candidate, request)
        weather, weather_reasons = _weather(candidate, request)
        if request.reference and candidate.lat is not None:
            distance = haversine_km(request.reference[0], request.reference[1], candidate.lat, candidate.lon)
            candidate.distance_km = round(distance, 3)
            # Within a destination, distance to its centre matters less than proximity "near me".
            scale = max((request.radius_km or 10.0) / (1.0 if request.reference_label and request.reference_label.endswith(" centre") else 2.0), 0.5)
            geographic = math.exp(-distance / scale)
        else:
            geographic = 0.5
        source_score = max((source_conf.get(s.source_type, 0.5) for s in candidate.sources), default=0.5)
        scores = {
            "relevance": relevance,
            "geographic": geographic,
            "quality": _quality(candidate),
            "open": open_score,
            "preference": preference,
            "weather": weather,
            "source_confidence": source_score,
        }
        candidate.scores = {key: round(value, 3) for key, value in scores.items()}
        candidate.score = round(sum(weights.get(key, 0.0) * value for key, value in scores.items()), 4)
        reasons = relevance_reasons + preference_reasons + open_reasons + weather_reasons
        if candidate.distance_km is not None and request.reference_label:
            reasons.append(f"{_format_distance(candidate.distance_km)} from {request.reference_label}")
        if candidate.star_rating:
            reasons.append(f"{candidate.star_rating}-star {candidate.property_type or 'stay'}")
        if candidate.guest_score is not None:
            reasons.append(f"Guest score {candidate.guest_score:.1f}/10" + (f" ({candidate.review_count:,} reviews)" if candidate.review_count else ""))
        elif candidate.rating is not None:
            reasons.append(f"Rated {candidate.rating:.1f}" + (f" ({candidate.review_count:,} reviews)" if candidate.review_count else ""))
        if candidate.cost_for_user.get("display") and candidate.cost_for_user.get("fits_budget") is None:
            reasons.append(f"{'Price per night' if candidate.cost_for_user['kind'] == 'per_night' else 'Entry'}: {candidate.cost_for_user['display']}")
        if request.user_point and candidate.lat is not None and request.reference_label and request.reference_label != "you":
            reasons.append(f"{_format_distance(haversine_km(request.user_point[0], request.user_point[1], candidate.lat, candidate.lon))} from you")
        mode_rules = config["ranking_modes"].get(constraints.ranking_mode or "") if constraints else None
        if mode_rules:
            if constraints.ranking_mode == "popular" and candidate.popularity_score is not None:
                candidate.score = round(candidate.score * 0.6 + 0.4 * candidate.popularity_score / 100, 4)
            bonus = set(candidate.tags) & set(mode_rules.get("bonus_tags", []))
            if bonus:
                candidate.score = round(candidate.score + 0.05 * len(bonus), 4)
                reasons.append({"hidden_gems": "Hidden gem", "local": "Local favourite", "popular": "Popular pick"}[constraints.ranking_mode] + f" ({', '.join(sorted(bonus)).replace('_', ' ')})")
        if constraints and constraints.raining and candidate.indoor:
            candidate.score = round(candidate.score + 0.05, 4)
            reasons.append("Indoors — good in the rain")
        if candidate.travel_min is not None and (constraints and (constraints.travel_mode or constraints.max_travel_min)):
            reasons.append(f"~{candidate.travel_min} min by {candidate.travel_mode} (estimate)")
        if candidate.conflicts:
            reasons.append("Sources disagree — verify before travelling")
        candidate.reasons = list(dict.fromkeys(reasons))
        candidate.confidence_detail = assess(candidate)
        candidate.bars = bars(candidate)
        ranked.append(candidate)
    ranked.sort(key=lambda c: (-(c.score or 0.0), c.distance_km if c.distance_km is not None else 1e9, c.name))
    return RankResult(ranked=ranked, filtered_out=filtered)
