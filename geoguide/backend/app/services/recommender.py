from __future__ import annotations

from typing import Any


class Recommender:
    def rank_places(self, profile: dict[str, Any], places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        for place in places:
            category = str(place.get('category', '')).lower()
            interests = profile.get('interests', {}) or {}
            score = 0.5

            for key, weight in interests.items():
                if key.lower() in category:
                    score += min(weight, 5) * 0.08

            if profile.get('budget') == 'moderate':
                score += 0.2
            if profile.get('pace') == 'relaxed':
                score += 0.1
            if 'nature' in category or 'park' in category:
                score += 0.15
            if 'museum' in category or 'heritage' in category:
                score += 0.15
            if place.get('distance_km') is not None and float(place['distance_km']) <= 2.0:
                score += 0.2

            reasons = ['Good fit for your current travel profile']
            if 'nature' in category:
                reasons.append('Nature-focused choice')
            if 'heritage' in category or 'museum' in category:
                reasons.append('Cultural interest match')
            if place.get('distance_km') is not None and float(place['distance_km']) <= 2.0:
                reasons.append('Close to your current location')

            ranked.append({**place, 'score': round(score, 3), 'reasons': reasons})
        return sorted(ranked, key=lambda item: item['score'], reverse=True)
