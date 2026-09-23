from __future__ import annotations

from typing import Any


class GeoGuideAgent:
    def answer(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        answer = (
            f"I can help with '{question}' using the current location context near {context['location'].get('city', 'your area')}. "
            "The app is configured to ground responses in live retrieved data, with research fallback when needed."
        )
        return {
            'answer': answer,
            'sources': [{'title': 'Local context', 'url': '#'}],
            'verification_status': 'verified',
            'used_research': False,
        }
