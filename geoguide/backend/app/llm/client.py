from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL_FAST


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or GROQ_API_KEY
        self.base_url = (base_url or GROQ_BASE_URL).rstrip('/')

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def chat(self, prompt: str, model: str | None = None, temperature: float = 0.2) -> str:
        if not self.api_key:
            return 'Groq key is not configured. Add GROQ_API_KEY to the environment.'

        payload = {
            'model': model or GROQ_MODEL_FAST,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': temperature,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(f'{self.base_url}/chat/completions', headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()
            return data['choices'][0]['message']['content']

    def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {'status': 'unconfigured', 'reason': 'missing GROQ_API_KEY'}

        try:
            payload = {
                'model': GROQ_MODEL_FAST,
                'messages': [{'role': 'user', 'content': 'Respond with OK'}],
                'max_tokens': 5,
            }
            with httpx.Client(timeout=20.0) as client:
                response = client.post(f'{self.base_url}/chat/completions', headers=self._headers(), json=payload)
                if response.status_code != 200:
                    return {'status': 'error', 'reason': response.text[:200]}
                return {'status': 'ok', 'provider': 'groq'}
        except Exception as exc:  # pragma: no cover - connection error path
            return {'status': 'error', 'reason': str(exc)}
