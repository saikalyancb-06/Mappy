"""SerpApi's structured Google Maps results as a PlaceSearchProvider.

This is SerpApi's interface over Google Maps search results (``engine=google_maps``,
``type=search`` for discovery; ``place_id``/``data_id`` for one place), not
Google's official Places API. Response fields follow SerpApi's documentation:
https://serpapi.com/google-maps-api · https://serpapi.com/maps-local-results ·
https://serpapi.com/maps-place-results. The key is used server-side only.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import CACHE_TTL_WEB_S
from app.places.providers.base import PlaceProviderError, PlaceResult, PlaceReview, PlaceSearchProvider, PlaceSearchResponse
from app.search.serpapi import SearchProviderError, SerpApiClient, client as default_client

logger = logging.getLogger(__name__)
PLACES_CACHE_TTL_S = max(CACHE_TTL_WEB_S, 6 * 3600)  # discovery results change slowly; details carry hours and reviews


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "")) if value is not None else None
    except (TypeError, ValueError):
        return None


def _hours(item: dict[str, Any]) -> dict[str, str] | None:
    value = item.get("operating_hours") or item.get("hours")
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if isinstance(v, str)} or None
    if isinstance(value, list):
        merged = {}
        for entry in value:
            if isinstance(entry, dict):
                merged.update({str(k): str(v) for k, v in entry.items() if isinstance(v, str)})
        return merged or None
    return None


def _description(item: dict[str, Any]) -> str | None:
    value = item.get("description")
    if isinstance(value, dict):
        value = value.get("snippet") or value.get("description")
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if isinstance(v, str))
    return value.strip() if isinstance(value, str) and value.strip() else None


def _images(item: dict[str, Any]) -> list[str]:
    urls = []
    for key in ("thumbnail", "image"):
        if isinstance(item.get(key), str):
            urls.append(item[key])
    for image in item.get("images") or []:
        if isinstance(image, dict):
            url = image.get("thumbnail") or image.get("image")
            if isinstance(url, str):
                urls.append(url)
    return [u for u in dict.fromkeys(urls) if u.startswith("https://")][:6]


def _reviews(item: dict[str, Any]) -> list[PlaceReview]:
    found: list[PlaceReview] = []
    user_reviews = item.get("user_reviews") if isinstance(item.get("user_reviews"), dict) else {}
    for group in ("most_relevant", "summary"):
        for review in user_reviews.get(group) or []:
            if not isinstance(review, dict):
                continue
            text = review.get("description") or review.get("snippet")
            if isinstance(text, str) and text.strip():
                found.append(PlaceReview(text=text.strip(), rating=_float(review.get("rating"))))
    listed = item.get("reviews_list") or (item.get("reviews") if isinstance(item.get("reviews"), list) else [])
    for review in listed if isinstance(listed, list) else []:
        if isinstance(review, dict) and isinstance(review.get("snippet"), str):
            found.append(PlaceReview(text=review["snippet"].strip(), rating=_float(review.get("rating"))))
    return found[:20]


def place_from_item(item: dict[str, Any]) -> PlaceResult | None:
    """One SerpApi Maps local/place result → PlaceResult (None without a name or identity)."""
    title = item.get("title") or item.get("name")
    external = item.get("place_id") or item.get("data_id") or item.get("data_cid")
    if not isinstance(title, str) or not title.strip() or not external:
        return None
    coordinates = item.get("gps_coordinates") or {}
    address = item.get("address")
    if isinstance(address, list):
        address = ", ".join(str(part) for part in address)
    link = item.get("website") if isinstance(item.get("website"), str) and item["website"].startswith("http") else None
    state = item.get("open_state") if isinstance(item.get("open_state"), str) else None
    closed_text = " ".join(str(x) for x in (state, item.get("business_status"), item.get("permanently_closed")) if x).lower()
    types = [str(t) for t in item.get("types") or [] if t]
    return PlaceResult(
        provider="serpapi_google_maps", external_id=str(external), data_id=item.get("data_id"), name=title.strip(),
        lat=_float(coordinates.get("latitude")), lon=_float(coordinates.get("longitude")), address=address if isinstance(address, str) else None,
        types=types, primary_type=item.get("type") if isinstance(item.get("type"), str) else (types[0] if types else None),
        rating=_float(item.get("rating")), review_count=_int(item.get("reviews") if not isinstance(item.get("reviews"), list) else None),
        price_text=item.get("price") if isinstance(item.get("price"), str) else None, hours=_hours(item), open_state=state,
        phone=item.get("phone") if isinstance(item.get("phone"), str) else None, website=link, description=_description(item),
        images=_images(item), url=link, reviews=_reviews(item),
        permanently_closed="permanently closed" in closed_text or item.get("permanently_closed") is True,
        raw=item,
    )


class SerpApiPlaceProvider(PlaceSearchProvider):
    name, label = "serpapi_google_maps", "Google Maps results via SerpApi"

    def __init__(self, client: SerpApiClient | None = None) -> None:
        self.client = client or default_client

    @property
    def configured(self) -> bool:
        return self.client.configured

    def _call(self, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            return self.client.fetch_json(params, PLACES_CACHE_TTL_S)
        except SearchProviderError as exc:
            raise PlaceProviderError(self.name, exc.code, exc.message, exc.retryable) from None

    def search(self, query: str, *, lat: float, lon: float, zoom: int = 13, limit: int = 20) -> PlaceSearchResponse:
        text = " ".join((query or "").split())
        if not text:
            raise PlaceProviderError(self.name, "invalid_query", "A search query is required.")
        payload, cached = self._call({"engine": "google_maps", "type": "search", "q": text, "ll": f"@{lat:.6f},{lon:.6f},{int(zoom)}z", "hl": "en"})
        items = payload.get("local_results") or []
        if not items and isinstance(payload.get("place_results"), dict):
            items = [payload["place_results"]]  # a query that names one place returns it directly
        results = [p for p in (place_from_item(item) for item in items if isinstance(item, dict)) if p][:limit]
        return PlaceSearchResponse(query=text, results=results, cached=cached)

    def details(self, place: PlaceResult) -> PlaceResult | None:
        params: dict[str, Any] = {"engine": "google_maps", "hl": "en"}
        if place.external_id and not place.external_id.startswith("0x"):
            params["place_id"] = place.external_id
        elif place.data_id and place.lat is not None and place.lon is not None:
            params.update(type="place", data=f"!4m5!3m4!1s{place.data_id}!8m2!3d{place.lat}!4d{place.lon}")
        else:
            return None
        payload, _ = self._call(params)
        item = payload.get("place_results")
        if not isinstance(item, dict):
            return None
        detailed = place_from_item({**item, "place_id": item.get("place_id") or place.external_id})
        if detailed and detailed.lat is None:
            detailed.lat, detailed.lon = place.lat, place.lon
        return detailed
