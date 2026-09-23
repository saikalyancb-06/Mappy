from __future__ import annotations

from typing import Any


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
