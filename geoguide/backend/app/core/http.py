"""Shared HTTP helpers for external providers."""
from __future__ import annotations

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


def get_json(provider: str, url: str, *, params: dict[str, Any] | None = None, timeout: float = 8.0, client: httpx.Client | None = None) -> Any:
    owns = client is None
    active = client or http_client(timeout)
    try:
        response = active.get(url, params=params)
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
    finally:
        if owns:
            active.close()
