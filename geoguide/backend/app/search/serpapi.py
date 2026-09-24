"""SerpApi client (server-side only; the key never reaches the frontend).

Web search is one evidence source among several. Results are returned in a
provider-neutral ``SearchResult`` and normalised into ``Candidate`` /
``Evidence`` by app.search.normalizer before anything else sees them.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import CACHE_TTL_WEB_S, SERPAPI_KEY, SERPAPI_TIMEOUT_SECONDS, SERPAPI_URL
from app.core.cache import cache_get, cache_set

logger = logging.getLogger(__name__)
ENGINES = {"google", "google_maps", "google_news", "google_events"}


class SearchProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, str]:
        return {"source": "serpapi", "code": self.code, "message": self.message}


@dataclass
class SearchResult:
    title: str
    url: str | None
    snippet: str
    source: str
    engine: str
    position: int | None = None
    published_date: str | None = None
    address: str | None = None
    rating: float | None = None
    review_count: int | None = None
    place_type: str | None = None
    place_types: list[str] = field(default_factory=list)
    hours_text: str | None = None
    open_state: str | None = None
    phone: str | None = None
    website: str | None = None
    place_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    event_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, [], "")}


@dataclass
class SearchResponse:
    query: str
    engine: str
    results: list[SearchResult]
    retrieved_at: str
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"query": self.query, "engine": self.engine, "results": [r.as_dict() for r in self.results], "retrieved_at": self.retrieved_at, "cached": self.cached}


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


class SerpApiClient:
    def __init__(self, api_key: str | None = None, timeout: float | None = None) -> None:
        self.api_key = SERPAPI_KEY if api_key is None else api_key
        self.timeout = timeout or SERPAPI_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, *, engine: str = "google", lat: float | None = None, lon: float | None = None, zoom: int = 14, limit: int = 10, freshness: str | None = None) -> SearchResponse:
        normalized = " ".join((query or "").split())
        if not normalized:
            raise SearchProviderError("invalid_query", "A search query is required.")
        if engine not in ENGINES:
            raise SearchProviderError("invalid_engine", f"Unsupported engine {engine}.")
        if not self.api_key:
            raise SearchProviderError("web_search_unavailable", "Web search is not configured (SERPAPI_KEY).")
        params: dict[str, Any] = {"engine": engine, "q": normalized, "api_key": self.api_key, "hl": "en"}
        if engine == "google_maps":
            params["type"] = "search"
            if lat is not None and lon is not None:
                params["ll"] = f"@{lat:.6f},{lon:.6f},{zoom}z"  # without it the query text carries the locality
        elif engine == "google":
            params["num"] = min(max(limit, 1), 20)
            if freshness:
                params["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m"}.get(freshness, freshness)
        cache_key = hashlib.sha256(repr(sorted((k, v) for k, v in params.items() if k != "api_key")).encode()).hexdigest()
        cached = cache_get("serpapi", cache_key)
        if cached is not None:
            payload = cached["data"]
            return SearchResponse(payload["query"], payload["engine"], [SearchResult(**item) for item in payload["results"]], payload["retrieved_at"], cached=True)

        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(SERPAPI_URL, params=params)
            if response.status_code == 429:
                raise SearchProviderError("rate_limited", "Web search rate limit reached.", retryable=True)
            if response.status_code in (401, 403):
                raise SearchProviderError("unauthorized", "Web search key was rejected.")
            if response.status_code >= 500:
                raise SearchProviderError("provider_failure", "Web search provider is temporarily unavailable.", retryable=True)
            response.raise_for_status()
            payload = response.json()
        except SearchProviderError:
            raise
        except httpx.TimeoutException:
            raise SearchProviderError("timeout", "Web search timed out.", retryable=True) from None
        except httpx.HTTPStatusError:
            raise SearchProviderError("provider_failure", "Web search provider rejected the request.") from None
        except httpx.HTTPError:
            raise SearchProviderError("unreachable", "Web search provider could not be reached.") from None
        except ValueError:
            raise SearchProviderError("malformed_response", "Web search returned an invalid response.") from None
        if not isinstance(payload, dict):
            raise SearchProviderError("malformed_response", "Web search returned an invalid response.")
        if payload.get("error") and not any(payload.get(key) for key in ("local_results", "organic_results", "news_results", "events_results", "place_results")):
            error = str(payload.get("error"))
            if "hasn't returned any results" in error or "no results" in error.lower():
                results: list[SearchResult] = []
            else:
                raise SearchProviderError("provider_failure", f"Web search error: {error[:120]}")
        else:
            results = self.parse(payload, engine, limit)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        search_response = SearchResponse(normalized, engine, results, retrieved_at)
        cache_set("serpapi", cache_key, {"query": normalized, "engine": engine, "results": [asdict(r) for r in results], "retrieved_at": retrieved_at}, CACHE_TTL_WEB_S, source="serpapi")
        logger.info("web_search engine=%s results=%d latency_ms=%d", engine, len(results), int((time.monotonic() - started) * 1000))
        return search_response

    @staticmethod
    def parse(payload: dict[str, Any], engine: str, limit: int = 10) -> list[SearchResult]:
        key = {"google_maps": "local_results", "google_news": "news_results", "google_events": "events_results"}.get(engine, "organic_results")
        items = payload.get(key) or []
        if engine == "google_maps" and not items and isinstance(payload.get("place_results"), dict):
            items = [payload["place_results"]]
        results: list[SearchResult] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            if not isinstance(title, str) or not title.strip():
                continue
            url = item.get("link") or item.get("website")
            if isinstance(url, str) and not url.startswith(("http://", "https://")):
                url = None
            coordinates = item.get("gps_coordinates") or {}
            date = item.get("date")
            event_date = None
            if isinstance(date, dict):
                event_date = date.get("when") or date.get("start_date")
                date = None
            address = item.get("address")
            if isinstance(address, list):
                address = ", ".join(str(part) for part in address)
            hours = item.get("hours") or (item.get("operating_hours") and "; ".join(f"{day}: {value}" for day, value in item["operating_hours"].items()))
            results.append(SearchResult(
                title=title.strip(),
                url=url,
                snippet=str(item.get("snippet") or item.get("description") or "").strip(),
                source=urlparse(url).netloc if url else ("Google Maps" if engine == "google_maps" else engine),
                engine=engine,
                position=item.get("position") or index,
                published_date=date if isinstance(date, str) else None,
                address=address,
                rating=_float(item.get("rating")),
                review_count=_int(item.get("reviews")),
                place_type=item.get("type"),
                place_types=[str(t) for t in item.get("types") or []],
                hours_text=hours if isinstance(hours, str) else None,
                open_state=item.get("open_state"),
                phone=item.get("phone"),
                website=item.get("website"),
                place_id=item.get("place_id") or item.get("data_id"),
                latitude=_float(coordinates.get("latitude")),
                longitude=_float(coordinates.get("longitude")),
                event_date=event_date,
            ))
            if len(results) >= limit:
                break
        return results


client = SerpApiClient()
