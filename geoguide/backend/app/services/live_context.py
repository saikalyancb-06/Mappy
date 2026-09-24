from __future__ import annotations

from time import monotonic
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from app.ingestion.geocode import reverse_geocode
from app.ingestion.overpass import build_overpass_query, normalize_overpass_response
from app.ingestion.weather import fetch_weather
from app.services.route_service import RouteService


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 300
_PROVIDER_TIMEOUT_SECONDS = 2.5
_ROUTE = RouteService()


def _cache_key(lat: float, lon: float, radius_km: float, include_places: bool) -> str:
    return f'{lat:.4f}:{lon:.4f}:{radius_km:.1f}:{include_places}'


def _get_json(client: httpx.Client, url: str, **params: Any) -> Any:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _weather_summary(weather: dict[str, Any]) -> str:
    current = weather.get('current', {})
    temperature = current.get('temperature_c')
    code = current.get('weather_code')
    labels = {0: 'Clear', 1: 'Mostly clear', 2: 'Partly cloudy', 3: 'Cloudy', 45: 'Foggy', 61: 'Rain', 80: 'Showers'}
    label = labels.get(code, 'Current conditions')
    return f'{temperature:.0f} C, {label}' if isinstance(temperature, (int, float)) else label


def get_live_context(lat: float, lon: float, city: str | None = None, radius_km: float = 2.0, include_places: bool = True) -> dict[str, Any]:
    key = _cache_key(lat, lon, radius_km, include_places)
    cached = _CACHE.get(key)
    if cached and monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    location = {'lat': lat, 'lon': lon, 'city': city or 'Current area'}
    places: list[dict[str, Any]] = []
    weather: dict[str, Any] = {}
    resolved: dict[str, Any] = {}
    provider_errors: list[str] = []

    def fetch_geocode() -> dict[str, Any]:
        with httpx.Client(timeout=_PROVIDER_TIMEOUT_SECONDS, headers={'User-Agent': 'GeoGuide/0.1 local place companion'}) as client:
            return reverse_geocode(_get_json(client, 'https://nominatim.openstreetmap.org/reverse', lat=lat, lon=lon, format='jsonv2', zoom=14))

    def fetch_weather_context() -> dict[str, Any]:
        with httpx.Client(timeout=_PROVIDER_TIMEOUT_SECONDS, headers={'User-Agent': 'GeoGuide/0.1 local place companion'}) as client:
            return fetch_weather(_get_json(
                client,
                'https://api.open-meteo.com/v1/forecast',
                latitude=lat,
                longitude=lon,
                current='temperature_2m,apparent_temperature,weather_code',
                hourly='temperature_2m,precipitation_probability',
                daily='temperature_2m_max,temperature_2m_min,sunrise,sunset',
                timezone='auto',
                forecast_days=1,
            ))

    def fetch_places() -> list[dict[str, Any]]:
        with httpx.Client(timeout=_PROVIDER_TIMEOUT_SECONDS, headers={'User-Agent': 'GeoGuide/0.1 local place companion'}) as client:
            return normalize_overpass_response(_get_json(client, 'https://overpass-api.de/api/interpreter', data=build_overpass_query(lat, lon, radius_km)))

    providers = {'geocode': fetch_geocode, 'weather': fetch_weather_context}
    if include_places:
        providers['places'] = fetch_places
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='geoguide-context') as executor:
        pending = {executor.submit(fetcher): name for name, fetcher in providers.items()}
        for future in as_completed(pending):
            provider = pending[future]
            try:
                value = future.result()
                if provider == 'geocode':
                    resolved = value
                elif provider == 'weather':
                    weather = value
                else:
                    places = value
            except (httpx.HTTPError, ValueError) as exc:
                provider_errors.append(f'{provider}: {type(exc).__name__}')

    if resolved:
        location.update({key: value for key, value in resolved.items() if value is not None})
    for place in places:
        place['distance_km'] = round(_ROUTE.haversine_km(lat, lon, float(place.get('lat') or lat), float(place.get('lon') or lon)), 2)
        place['source'] = 'OpenStreetMap'

    result = {
        'location': location,
        'area': {
            'id': f"area-{lat:.4f}-{lon:.4f}",
            'name': location.get('city') or resolved.get('region') or 'Current area',
            'region': resolved.get('region'),
            'country': resolved.get('country'),
            'country_code': resolved.get('country_code'),
        },
        'weather': {**weather, 'summary': _weather_summary(weather)} if weather else {'summary': 'Weather unavailable'},
        'places': places,
        'provider_errors': provider_errors,
    }
    _CACHE[key] = (monotonic(), result)
    return result


def safe_location(lat: Any, lon: Any, city: Any = None) -> dict[str, Any]:
    try:
        safe_lat = float(lat)
        safe_lon = float(lon)
        if not (-90 <= safe_lat <= 90 and -180 <= safe_lon <= 180):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('A valid location is required.') from None
    return {'lat': safe_lat, 'lon': safe_lon, 'city': city if isinstance(city, str) and city else None}
