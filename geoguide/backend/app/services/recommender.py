from __future__ import annotations

from typing import Any


class Recommender:
    def rank_places(self, profile: dict[str, Any], places: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        interests = profile.get('interests', {}) or {}
        # Sorted user interests from highest priority/order
        interest_list = [k.lower() for k, v in sorted(interests.items(), key=lambda x: x[1], reverse=True)] if isinstance(interests, dict) else [str(x).lower() for x in interests]

        category_aliases = {
            'nature': ['park', 'nature', 'garden', 'lake', 'green', 'outdoor', 'viewpoint'],
            'heritage': ['heritage', 'historic', 'history', 'monument', 'temple', 'museum', 'palace', 'fort'],
            'food': ['restaurant', 'food', 'dining', 'bakery', 'eatery', 'snack', 'diner'],
            'cafes': ['cafe', 'coffee', 'tea', 'bakery', 'bistro'],
            'culture': ['culture', 'theatre', 'theater', 'arts', 'gallery', 'museum', 'exhibition'],
            'shopping': ['mall', 'shopping', 'market', 'store', 'bazaar'],
            'nightlife': ['pub', 'bar', 'club', 'brewery', 'lounge', 'nightclub'],
            'art': ['art', 'gallery', 'craft', 'studio', 'museum'],
        }

        for place in places:
            name = str(place.get('name', '')).lower()
            category = str(place.get('category', '')).lower()
            rating = float(place.get('rating') or 0.0)
            distance_km = float(place['distance_km']) if place.get('distance_km') is not None else 5.0
            
            score = 0.5
            matched_interest = None
            priority_rank = 999

            # Check matches against prioritized user interests
            for idx, interest in enumerate(interest_list):
                aliases = category_aliases.get(interest, [interest])
                if any(alias in category or alias in name for alias in aliases):
                    if matched_interest is None:
                        matched_interest = interest
                        priority_rank = idx
                    # Earlier preference gets higher boost
                    priority_bonus = max(0.1, 0.4 - (idx * 0.1))
                    score += priority_bonus
                    break

            if profile.get('budget') == 'moderate':
                score += 0.05
            if profile.get('pace') == 'relaxed':
                score += 0.05

            # Higher rating boost
            if rating > 0:
                score += (rating / 5.0) * 0.25

            # Proximity boost: closer places get priority
            if distance_km <= 1.0:
                score += 0.3
            elif distance_km <= 2.5:
                score += 0.2
            elif distance_km <= 5.0:
                score += 0.1

            # Build tailored reasons
            reasons = []
            if matched_interest:
                reasons.append(f"Matches your interest in {matched_interest.capitalize()}")
            elif 'park' in category or 'nature' in category:
                reasons.append("Great outdoor & scenic spot to unwind")
            elif 'cafe' in category or 'restaurant' in category:
                reasons.append("Great place to sit, eat, and relax")
            elif rating >= 4.0:
                reasons.append(f"Top-rated local favorite ({rating}★)")
            else:
                reasons.append("Recommended nearby place to visit")

            if distance_km <= 1.5:
                reasons.append(f"Only {distance_km:.1f} km from your current spot")

            ranked.append({**place, 'score': round(score, 3), 'priority_rank': priority_rank, 'reasons': reasons})

        # Sort primarily by whether it matched user's preferred interest order, then overall score
        return sorted(ranked, key=lambda item: (item['priority_rank'], -item['score'], item.get('distance_km') or 999))

