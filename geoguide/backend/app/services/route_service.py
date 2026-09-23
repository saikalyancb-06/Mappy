from __future__ import annotations

from math import radians, sin, cos, sqrt, atan2


class RouteService:
    def haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        phi1 = radians(lat1)
        phi2 = radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
        return 2 * r * atan2(sqrt(a), sqrt(1 - a))

    def estimate_travel(self, from_point: dict, to_point: dict, mode: str = 'walk') -> dict:
        distance_km = self.haversine_km(
            float(from_point.get('lat', 0.0)),
            float(from_point.get('lon', 0.0)),
            float(to_point.get('lat', 0.0)),
            float(to_point.get('lon', 0.0)),
        )
        speeds = {'walk': 4.0, 'bike': 12.0, 'drive': 30.0, 'transit': 14.0}
        speed = speeds.get(mode, 4.0)
        duration_minutes = max(5, int((distance_km / speed) * 60))
        return {
            'mode': mode,
            'distance_km': round(distance_km, 2),
            'duration_minutes': duration_minutes,
            'estimate': True,
        }
