"""Normalise raw web results into canonical Candidates and Evidence."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.core.rules import taxonomy
from app.core.text import normalize
from app.geo.distance import haversine_km, valid_coordinates
from app.models import DEFAULT_SOURCE_CONFIDENCE, Candidate, SourceRef
from app.search.serpapi import SearchResult


def classify_category(texts: list[str], fallback: str | None = None) -> str | None:
    """Map provider type strings ("South Indian restaurant", "Hindu temple") onto the taxonomy."""
    joined = " " + normalize(" ".join(t for t in texts if t)) + " "
    best: tuple[int, str] | None = None
    for category_id, entry in taxonomy()["categories"].items():
        for synonym in entry["synonyms"]:
            target = normalize(synonym)
            if target and f" {target} " in joined and (best is None or len(target) > best[0]):
                best = (len(target), category_id)
    return best[1] if best else fallback


def _open_status(result: SearchResult) -> tuple[str, dict[str, Any]]:
    state = (result.open_state or "").strip()
    if not state:
        return "unknown", {}
    lowered = state.lower()
    if lowered.startswith("open") or "open now" in lowered or "open 24" in lowered or lowered.startswith("closes"):
        return "open", {"provider_text": state}
    if lowered.startswith("closed") or "opens" in lowered or "temporarily closed" in lowered or "permanently closed" in lowered:
        return "closed", {"provider_text": state, "permanently_closed": "permanently" in lowered}
    return "unknown", {"provider_text": state}


def candidate_from_web(result: SearchResult, retrieved_at: str, reference: tuple[float, float] | None = None, category_hint: str | None = None) -> Candidate | None:
    """A map result becomes a place candidate; results without coordinates cannot be places."""
    if result.engine != "google_maps" or not valid_coordinates(result.latitude, result.longitude):
        return None
    identity = result.place_id or f"{normalize(result.title)}|{result.latitude:.5f}|{result.longitude:.5f}"
    candidate_id = "web-" + hashlib.sha1(identity.encode()).hexdigest()[:14]
    status, detail = _open_status(result)
    category = classify_category([result.place_type or "", *result.place_types, result.title], fallback=category_hint)
    kind = taxonomy()["categories"].get(category or "", {}).get("kind")
    distance = haversine_km(reference[0], reference[1], result.latitude, result.longitude) if reference else None
    return Candidate(
        id=candidate_id,
        name=result.title,
        category=category,
        kind=kind,
        lat=result.latitude,
        lon=result.longitude,
        address=result.address,
        description=result.snippet or None,
        rating=result.rating,
        review_count=result.review_count,
        opening_hours_text=result.hours_text,
        open_status=status,
        open_detail=detail,
        phone=result.phone,
        website=result.website,
        sources=[SourceRef(source_type="serpapi_maps", source="Google Maps via SerpApi", source_url=result.website or result.url, source_id=result.place_id, retrieved_at=retrieved_at, confidence=DEFAULT_SOURCE_CONFIDENCE["serpapi_maps"])],
        confidence=DEFAULT_SOURCE_CONFIDENCE["serpapi_maps"],
        distance_km=round(distance, 3) if distance is not None else None,
    )


def web_evidence(result: SearchResult, retrieved_at: str) -> dict[str, Any]:
    """WebEvidence: title, url, snippet, source domain, retrieval time, confidence."""
    domain = result.source
    return {
        "title": result.title,
        "url": result.url,
        "snippet": re.sub(r"\s+", " ", result.snippet or "").strip()[:500],
        "source_domain": domain,
        "engine": result.engine,
        "published": result.published_date or result.event_date,
        "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        "confidence": DEFAULT_SOURCE_CONFIDENCE["serpapi_web"],
    }


def candidate_from_hotel(result: SearchResult, retrieved_at: str, reference: tuple[float, float] | None = None) -> Candidate | None:
    """A Google Hotels property becomes a stay candidate with a live nightly rate (when the provider gives one)."""
    if result.engine != "google_hotels" or not valid_coordinates(result.latitude, result.longitude):
        return None
    identity = result.place_id or f"{normalize(result.title)}|{result.latitude:.5f}|{result.longitude:.5f}"
    distance = haversine_km(reference[0], reference[1], result.latitude, result.longitude) if reference else None
    return Candidate(
        id="web-" + hashlib.sha1(identity.encode()).hexdigest()[:14],
        name=result.title,
        category="hotel",
        kind="stay",
        lat=result.latitude,
        lon=result.longitude,
        description=result.snippet or None,
        rating=result.rating,
        review_count=result.review_count,
        star_rating=result.hotel_class,
        property_type=result.place_type,
        checkin_time=result.checkin_time,
        checkout_time=result.checkout_time,
        price_per_night=result.price_per_night,
        price_currency=result.price_currency,
        price_source="Google Hotels via SerpApi" if result.price_per_night else None,
        price_retrieved_at=retrieved_at if result.price_per_night else None,
        website=result.url,
        sources=[SourceRef(source_type="serpapi_hotels", source="Google Hotels via SerpApi", source_url=result.url, source_id=result.place_id, retrieved_at=retrieved_at, confidence=DEFAULT_SOURCE_CONFIDENCE["serpapi_maps"])],
        confidence=DEFAULT_SOURCE_CONFIDENCE["serpapi_maps"],
        distance_km=round(distance, 3) if distance is not None else None,
    )
