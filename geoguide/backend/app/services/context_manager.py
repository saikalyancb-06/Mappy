from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ContextManager:
    def build_context(self, user_profile: dict[str, Any] | None = None, location: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        local_time = now.astimezone().isoformat(timespec='minutes')
        profile = user_profile or {
            'language': 'en',
            'interests': {'nature': 4, 'history': 3, 'food': 4},
            'budget': 'moderate',
            'pace': 'balanced',
            'accessibility': [],
            'likes': [],
            'dislikes': [],
        }
        loc = location or {'lat': 12.9716, 'lon': 77.5946, 'city': 'Bengaluru'}
        return {
            'user_profile': profile,
            'location': loc,
            'area': {'id': f"area-{loc.get('lat', 0)}-{loc.get('lon', 0)}", 'name': loc.get('city', 'Current area')},
            'local_time': local_time,
            'season': 'generic',
            'daylight_left': 'unknown',
            'weather': {'summary': 'data unavailable'},
            'events': [],
            'advisories': [],
            'session_filters': {},
            'current_plan': None,
        }
