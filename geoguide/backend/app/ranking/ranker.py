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
        return min(1.0, rules["unrated_score"] + tag_bonus)
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
    likes, dislikes = set(user.get("likes") or []), set(user.get("dislikes") or [])
    if candidate.id in dislikes or candidate.category in dislikes:
        return 0.0, ["You marked this type as a dislike"]
    if candidate.id in likes or candidate.category in likes:
        score += 1.0
        parts += 1
        reasons.append("Similar to places you liked")
    budget = user.get("budget")
    if budget and candidate.price_level is not None:
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
    for candidate in candidates:
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
        if candidate.rating is not None:
            reasons.append(f"Rated {candidate.rating:.1f}" + (f" ({candidate.review_count:,} reviews)" if candidate.review_count else ""))
        if request.user_point and candidate.lat is not None and request.reference_label and request.reference_label != "you":
            reasons.append(f"{_format_distance(haversine_km(request.user_point[0], request.user_point[1], candidate.lat, candidate.lon))} from you")
        candidate.reasons = list(dict.fromkeys(reasons))
        ranked.append(candidate)
    ranked.sort(key=lambda c: (-(c.score or 0.0), c.distance_km if c.distance_km is not None else 1e9, c.name))
    return RankResult(ranked=ranked, filtered_out=filtered)
