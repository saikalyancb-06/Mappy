from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import SERPAPI_KEY

logger = logging.getLogger(__name__)
_SERPAPI_URL = 'https://serpapi.com/search.json'
_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, 'SearchResponse']] = {}
_CACHE_LOCK = threading.Lock()


class SearchProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    position: int | None = None
    thumbnail: str | None = None
    published_date: str | None = None
    content: str | None = None
    address: str | None = None
    rating: float | None = None
    opening_hours: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def as_dict(self) -> dict[str, Any]:
        values = self.__dict__
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class SearchResponse:
    query: str
    engine: str
    results: tuple[SearchResult, ...]
    retrieved_at: str
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            'query': self.query,
            'engine': self.engine,
            'results': [result.as_dict() for result in self.results],
            'retrieved_at': self.retrieved_at,
            'cached': self.cached,
        }


def select_engine(query: str) -> str:
    words = set(re.findall(r'[a-z0-9]+', query.lower()))
    if words & {'news', 'event', 'events', 'festival', 'concert', 'happening', 'today', 'tonight'}:
        return 'google_news'
    if words & {'photo', 'photography', 'image', 'images', 'visual'}:
        return 'google_images'
    if words & {'near', 'nearby', 'shop', 'shops', 'cafe', 'cafes', 'restaurant', 'restaurants', 'hotel', 'hotels', 'atm', 'atms', 'pharmacy', 'pharmacies', 'park', 'parks'}:
        return 'google_maps'
    return 'google'


class SerpApiSearchProvider:
    def __init__(self, api_key: str | None = None, timeout: float = 8.0) -> None:
        self.api_key = api_key if api_key is not None else SERPAPI_KEY
        self.timeout = timeout

    def search(self, query: str, location: dict[str, Any] | None = None, freshness: str | None = None, engine: str | None = None, limit: int = 8) -> SearchResponse:
        normalized_query = ' '.join(query.split())
        if not normalized_query:
            raise SearchProviderError('invalid_query', 'A live search query is required.')
        if not self.api_key:
            raise SearchProviderError('web_search_unavailable', 'No web search provider configured.')
        selected_engine = engine or select_engine(normalized_query)
        limit = min(max(int(limit), 1), 20)
        cache_key = self._cache_key(normalized_query, selected_engine, location, freshness, limit)
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                response = cached[1]
                return SearchResponse(response.query, response.engine, response.results, response.retrieved_at, cached=True)

        params: dict[str, Any] = {'engine': selected_engine, 'q': normalized_query, 'api_key': self.api_key, 'num': limit}
        if location:
            if location.get('lat') is not None and location.get('lon') is not None:
                params['ll'] = f"@{location['lat']},{location['lon']},12z"
                if selected_engine == 'google_maps':
                    # SerpApi recommends the 'nearby' parameter for Maps "near me" searches.
                    # 'll' alone does not guarantee results fall within the supplied location.
                    params['nearby'] = f"{location['lat']},{location['lon']}"
            else:
                params['location'] = self._location_text(location)
        if freshness and selected_engine in {'google', 'google_news'}:
            params['tbs'] = {'day': 'qdr:d', 'week': 'qdr:w', 'month': 'qdr:m'}.get(freshness, freshness)

        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(_SERPAPI_URL, params=params)
            if response.status_code == 429:
                raise SearchProviderError('rate_limited', 'Live search rate limit reached.', retryable=True)
            if response.status_code >= 500:
                raise SearchProviderError('provider_failure', 'Live search provider is temporarily unavailable.', retryable=True)
            response.raise_for_status()
            payload = response.json()
        except SearchProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise SearchProviderError('timeout', 'Live search timed out.', retryable=True) from exc
        except httpx.HTTPStatusError:
            raise SearchProviderError('provider_failure', 'Live search provider rejected the request.') from None
        except httpx.HTTPError:
            raise SearchProviderError('provider_failure', 'Live search provider could not be reached.') from None
        except ValueError:
            raise SearchProviderError('malformed_response', 'Live search returned an invalid response.') from None

        if not isinstance(payload, dict) or payload.get('error'):
            raise SearchProviderError('provider_failure', 'Live search provider returned an error.')
        results = self._parse_results(payload, selected_engine, limit)
        search_response = SearchResponse(normalized_query, selected_engine, tuple(results), datetime.now(timezone.utc).isoformat())
        with _CACHE_LOCK:
            _CACHE[cache_key] = (time.monotonic(), search_response)
        logger.info('web_search provider=serpapi engine=%s result_count=%d latency_ms=%d success=true', selected_engine, len(results), int((time.monotonic() - started) * 1000))
        return search_response

    def extract(self, result: SearchResult, limit: int = 12000) -> SearchResult:
        if not result.url:
            return result
        try:
            with httpx.Client(timeout=min(self.timeout, 5.0), follow_redirects=True, headers={'User-Agent': 'GeoGuide/0.1 evidence fetcher'}) as client:
                response = client.get(result.url)
                response.raise_for_status()
            parser = _VisibleTextParser()
            parser.feed(response.text)
            content = ' '.join(parser.parts)[:limit]
            return SearchResult(result.title, result.url, result.snippet, result.source, result.position, result.thumbnail, result.published_date, content, result.address, result.rating, result.opening_hours, result.latitude, result.longitude)
        except (httpx.HTTPError, ValueError):
            logger.info('web_extract_failed source=%s', result.source)
            return result

    @staticmethod
    def filter_by_distance(results: list[SearchResult], latitude: float, longitude: float, radius_km: float) -> list[SearchResult]:
        from app.services.route_service import RouteService
        route = RouteService()
        filtered = []
        for result in results:
            if result.latitude is None or result.longitude is None or route.haversine_km(latitude, longitude, result.latitude, result.longitude) <= radius_km:
                filtered.append(result)
        return filtered

    @staticmethod
    def _parse_results(payload: dict[str, Any], engine: str, limit: int) -> list[SearchResult]:
        key = {'google_maps': 'local_results', 'google_news': 'news_results', 'google_images': 'images_results'}.get(engine, 'organic_results')
        raw_items = payload.get(key) or []
        if engine == 'google_maps' and not raw_items and isinstance(payload.get('place_results'), dict):
            raw_items = [payload['place_results']]
        results: list[SearchResult] = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            url = item.get('link') or item.get('website') or item.get('share_link') or item.get('original')
            title = item.get('title') or item.get('name')
            if not isinstance(url, str) or not url.startswith(('http://', 'https://')) or not isinstance(title, str) or not title.strip():
                continue
            coordinates = item.get('gps_coordinates') or {}
            results.append(SearchResult(title.strip(), url, str(item.get('snippet') or item.get('description') or '').strip(), urlparse(url).netloc, item.get('position') or index, item.get('thumbnail'), item.get('date') or item.get('published_date'), None, item.get('address'), item.get('rating'), item.get('hours'), coordinates.get('latitude'), coordinates.get('longitude')))
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _location_text(location: dict[str, Any]) -> str:
        return ', '.join(str(location[key]) for key in ('city', 'region', 'country') if location.get(key)) or 'current location'

    @staticmethod
    def _cache_key(query: str, engine: str, location: dict[str, Any] | None, freshness: str | None, limit: int) -> str:
        location_key = tuple((key, location.get(key)) for key in ('lat', 'lon', 'city', 'region', 'country')) if location else ()
        return hashlib.sha256(repr((query.lower(), engine, location_key, freshness, limit)).encode()).hexdigest()


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {'script', 'style', 'nav', 'footer', 'header', 'noscript'}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {'script', 'style', 'nav', 'footer', 'header', 'noscript'} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())
