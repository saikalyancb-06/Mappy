from __future__ import annotations

from typing import Any

import httpx


def reverse_geocode(payload: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {
            'city': None,
            'region': None,
            'country': None,
            'lat': None,
            'lon': None,
            'timezone': None,
            'currency': None,
            'country_code': None,
        }

    item = payload[0] if isinstance(payload, list) else payload
    address = item.get('address', {}) if isinstance(item, dict) else {}
    city = address.get('city') or address.get('town') or address.get('village') or item.get('name')
    region = address.get('state') or address.get('region') or address.get('county')
    country = address.get('country')
    lat = float(item.get('lat')) if item.get('lat') is not None else None
    lon = float(item.get('lon')) if item.get('lon') is not None else None

    return {
        'city': city,
        'region': region,
        'country': country,
        'lat': lat,
        'lon': lon,
        'timezone': None,
        'currency': None,
        'country_code': address.get('country_code'),
    }


def forward_geocode(query: str, client: httpx.Client | None = None) -> dict[str, Any] | None:
    search = query.strip()
    if not search:
        return None
    owns_client = client is None
    active_client = client or httpx.Client(timeout=8.0, headers={'User-Agent': 'GeoGuide/0.1 local place companion'})
    try:
        response = active_client.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': search, 'format': 'jsonv2', 'limit': 1, 'addressdetails': 1},
        )
        response.raise_for_status()
        return reverse_geocode(response.json())
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            active_client.close()
