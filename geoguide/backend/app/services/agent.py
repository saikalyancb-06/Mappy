from __future__ import annotations

from typing import Any

from app.llm.client import LLMClient


class GeoGuideAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()

    def answer(self, question: str, context: dict[str, Any], evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        evidence = evidence or []
        sources = [{'title': item.get('name', 'Local place'), 'url': item.get('source_url') or item.get('source', 'structured store')} for item in evidence]
        if not evidence:
            return {
                'answer': 'No verified places matched this location and query.',
                'sources': [],
                'verification_status': 'no_results',
                'used_research': False,
            }
        city = context.get('location', {}).get('city') or 'your area'
        clean_evidence = []
        for item in evidence:
            clean_item = {'name': item.get('name') or item.get('title')}
            if item.get('category'):
                clean_item['category'] = item['category']
            if item.get('address'):
                clean_item['address'] = item['address']
            if item.get('distance_km') is not None:
                clean_item['distance_km'] = f"{float(item['distance_km']):.1f} km"
            if item.get('rating'):
                clean_item['rating'] = item['rating']
            if item.get('opening_hours'):
                clean_item['opening_hours'] = item['opening_hours']
            if item.get('description'):
                clean_item['description'] = item['description'][:150]
            clean_evidence.append(clean_item)

        prompt = (
            f"You are GeoGuide, a knowledgeable and friendly local companion for {city}.\n"
            "Answer the user's question clearly, warmly, and helpfully using the verified places in EVIDENCE.\n"
            "Guidelines:\n"
            "- Highlight top places with their names in bold.\n"
            "- Mention what each place is (cafe, park, restaurant, attraction, etc.), why it's great, its approximate distance or neighborhood, and rating if available.\n"
            "- Do not dump raw coordinates, json objects, or system keys.\n"
            "- If the user asks for suggestions or what's good right now, give a structured, appealing list with quick tips.\n"
            "- Keep it engaging, easy to read, and grounded in the evidence.\n\n"
            f"USER QUESTION: {question}\n"
            f"EVIDENCE: {clean_evidence}\n"
        )
        try:
            generated = self.llm.chat(prompt)
        except Exception as exc:
            generated = f'Grounded answer generation is temporarily unavailable: {exc}'
        if generated.startswith('Groq key is not configured') or generated.startswith('Grounded answer generation is temporarily unavailable'):
            names = ', '.join(item.get('name', 'Unnamed place') for item in evidence[:5])
            answer = f'Verified local evidence is available for: {names}.' if names else 'I could not verify any matching local places yet.'
            verification = 'partial'
        else:
            answer = generated
            verification = 'grounded'
        return {
            'answer': answer,
            'sources': sources,
            'verification_status': verification,
            'used_research': bool(evidence),
        }
