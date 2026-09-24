from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


class LocationContextError(ValueError):
    pass


def validate_location_context(value: dict[str, Any] | None, *, max_age_seconds: int = 3600) -> dict[str, Any]:
    raw = value or {}
    try:
        latitude = float(raw['latitude'] if 'latitude' in raw else raw['lat'])
        longitude = float(raw['longitude'] if 'longitude' in raw else raw['lon'])
    except (KeyError, TypeError, ValueError):
        raise LocationContextError('A valid latitude and longitude are required.') from None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise LocationContextError('The location coordinates are outside valid geographic ranges.')
    timestamp = raw.get('timestamp')
    if timestamp:
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
            if age > max_age_seconds or age < -60:
                raise LocationContextError('The location is stale.')
        except LocationContextError:
            raise
        except ValueError:
            raise LocationContextError('The location timestamp is invalid.') from None
    source = str(raw.get('source') or 'device')
    if source not in {'device', 'user_explicit_query', 'selected_map_location', 'saved_destination', 'resolved_place'}:
        raise LocationContextError('The location source is invalid.')
    return {
        'latitude': latitude,
        'longitude': longitude,
        'accuracy_meters': float(raw['accuracy_meters']) if raw.get('accuracy_meters') is not None else (float(raw['accuracy']) if raw.get('accuracy') is not None else None),
        'source': source,
        'timestamp': str(timestamp or datetime.now(timezone.utc).isoformat()),
        'confidence': float(raw.get('confidence', 1.0)),
        'city': raw.get('city'),
    }


def extract_explicit_destination(query: str) -> str | None:
    # Strip trailing temporal qualifiers before matching so they don't bleed into the destination
    _TEMPORAL = re.compile(r'\s*\b(?:right\s+now|now|today|tonight|currently|at\s+the\s+moment)\b\s*[?.!]*$', re.IGNORECASE)
    normalised = _TEMPORAL.sub('', query.strip()).strip(' ?.,!')
    match = re.search(r'\b(?:in|at|near)\s+(.+?)$', normalised, re.IGNORECASE)
    if not match:
        return None
    candidate = match.group(1).strip(' ?.,!')
    # Block all "near/at/in me" variants including "me" alone
    _SELF_REFS = {'me', 'my current location', 'here', 'my location', 'this location'}
    if not candidate or candidate.casefold() in _SELF_REFS:
        return None
    return candidate