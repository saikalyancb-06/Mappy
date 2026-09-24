"""Shared HTTP helpers for external providers.

One pooled, thread-safe client is reused for every provider call, so repeat calls
skip DNS/TCP/TLS setup (keep-alive). Timeouts are set per request.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx

from app.config import HTTP_USER_AGENT


class ProviderError(RuntimeError):
    """An external provider failed. Callers degrade instead of crashing."""

    def __init__(self, provider: str, code: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"source": self.provider, "code": self.code, "message": self.message}


def http_client(timeout: float = 8.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": HTTP_USER_AGENT}, follow_redirects=True)


_shared: httpx.Client | None = None
_shared_lock = threading.Lock()


def shared_client() -> httpx.Client:
    """The process-wide pooled client (created lazily)."""
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = httpx.Client(
                    timeout=10.0, headers={"User-Agent": HTTP_USER_AGENT}, follow_redirects=True,
                    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=60.0),
                )
    return _shared


def get_json(provider: str, url: str, *, params: dict[str, Any] | None = None, timeout: float = 8.0, client: httpx.Client | None = None) -> Any:
    active = client or shared_client()
    try:
        response = active.get(url, params=params, timeout=timeout)
        if response.status_code == 429:
            raise ProviderError(provider, "rate_limited", "rate limit reached")
        response.raise_for_status()
        return response.json()
    except ProviderError:
        raise
    except httpx.TimeoutException:
        raise ProviderError(provider, "timeout", "request timed out") from None
    except httpx.HTTPStatusError as exc:
        raise ProviderError(provider, "http_error", f"HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        raise ProviderError(provider, "unreachable", type(exc).__name__) from None
    except ValueError:
        raise ProviderError(provider, "malformed_response", "invalid JSON") from None
