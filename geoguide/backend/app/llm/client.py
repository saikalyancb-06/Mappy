"""Groq (OpenAI-compatible) chat client. The key stays server-side."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL_FAST, GROQ_MODEL_REASONING, LLM_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LLMUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"source": "groq", "code": self.code, "message": self.message}


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: float | None = None) -> None:
        self.api_key = GROQ_API_KEY if api_key is None else api_key
        self.base_url = (base_url or GROQ_BASE_URL).rstrip("/")
        self.timeout = timeout or LLM_TIMEOUT_SECONDS
        self.fast_model = GROQ_MODEL_FAST
        self.reasoning_model = GROQ_MODEL_REASONING

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, str]], *, model: str | None = None, temperature: float = 0.2, max_tokens: int = 700, json_mode: bool = False) -> str:
        if not self.api_key:
            raise LLMUnavailable("unconfigured", "GROQ_API_KEY is not set.")
        payload: dict[str, Any] = {"model": model or self.reasoning_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload)
        except httpx.TimeoutException:
            raise LLMUnavailable("timeout", "The language model timed out.") from None
        except httpx.HTTPError as exc:
            raise LLMUnavailable("unreachable", f"The language model could not be reached ({type(exc).__name__}).") from None
        if response.status_code == 429:
            raise LLMUnavailable("rate_limited", "The language model rate limit was reached.")
        if response.status_code in (401, 403):
            raise LLMUnavailable("unauthorized", "The language model key was rejected.")
        if response.status_code >= 400:
            raise LLMUnavailable("provider_error", f"The language model returned HTTP {response.status_code}.")
        try:
            content = response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMUnavailable("malformed_response", "The language model returned an invalid response.") from None
        logger.info("llm_call model=%s latency_ms=%d", payload["model"], int((time.monotonic() - started) * 1000))
        return _THINK.sub("", content).strip()

    def chat_json(self, messages: list[dict[str, str]], *, model: str | None = None, max_tokens: int = 400) -> dict[str, Any]:
        text = self.chat(messages, model=model or self.fast_model, temperature=0.0, max_tokens=max_tokens, json_mode=True)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise LLMUnavailable("malformed_response", "Expected JSON from the language model.")
        try:
            return json.loads(match.group(0))
        except ValueError:
            raise LLMUnavailable("malformed_response", "Expected JSON from the language model.") from None

    def status(self) -> dict[str, Any]:
        return {"configured": self.configured, "fast_model": self.fast_model, "reasoning_model": self.reasoning_model}


llm = LLMClient()
