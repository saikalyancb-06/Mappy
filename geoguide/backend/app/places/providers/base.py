"""Provider-neutral place search interface.

GeoGuide never lets a provider's response shape leak past this module: every
provider returns ``PlaceResult`` objects, which ``app.places.normalize`` turns
into GeoGuide's own POI fields.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class PlaceProviderError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider, self.code, self.message, self.retryable = provider, code, message, retryable

    def as_dict(self) -> dict[str, str]:
        return {"source": self.provider, "code": self.code, "message": self.message}


@dataclass
class PlaceReview:
    text: str
    rating: float | None = None


@dataclass
class PlaceResult:
    provider: str
    external_id: str  # the provider's stable place identifier
    name: str
    lat: float | None
    lon: float | None
    data_id: str | None = None  # secondary identifier used for detail look-ups
    address: str | None = None
    types: list[str] = field(default_factory=list)
    primary_type: str | None = None
    rating: float | None = None
    review_count: int | None = None
    price_text: str | None = None
    hours: dict[str, str] | None = None  # {"monday": "9 AM–5 PM", …}
    open_state: str | None = None
    phone: str | None = None
    website: str | None = None
    description: str | None = None
    images: list[str] = field(default_factory=list)
    url: str | None = None
    reviews: list[PlaceReview] = field(default_factory=list)
    permanently_closed: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class PlaceSearchResponse:
    query: str
    results: list[PlaceResult]
    cached: bool = False


class PlaceSearchProvider(ABC):
    name: str = "provider"
    label: str = "Place provider"

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    def search(self, query: str, *, lat: float, lon: float, zoom: int = 13, limit: int = 20) -> PlaceSearchResponse:
        """Places matching a text query around a point."""

    def details(self, place: PlaceResult) -> PlaceResult | None:
        """Full details (hours, reviews, images) for one place; None when unavailable."""
        return None
