"""Event provider interface. Add a provider by implementing this; nothing downstream changes."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.events.model import EventQuery, NormalisedEvent


@dataclass
class ProviderResult:
    provider: str
    label: str
    status: str = "ok"  # ok | not_configured | not_applicable | error
    events: list[NormalisedEvent] = field(default_factory=list)
    raw_count: int = 0
    dropped: dict[str, int] = field(default_factory=dict)  # reason → count (no_date, out_of_range, not_an_event, cancelled…)
    error: dict[str, str] | None = None
    latency_ms: int = 0
    cached: bool = False
    queries: list[str] = field(default_factory=list)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "source": self.label, "status": self.status, "found": self.raw_count, "kept": len(self.events), "dropped": self.dropped, "error": self.error, "latency_ms": self.latency_ms, "cached": self.cached, "queries": self.queries}


class EventProvider(ABC):
    name: str = "provider"
    label: str = "Provider"

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    def search_events(self, query: EventQuery) -> ProviderResult:
        """Events for the query's place and time range (already normalised)."""

    def get_event(self, event_id: str, query: EventQuery | None = None) -> NormalisedEvent | None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.configured}

    def _timed(self) -> float:
        return time.monotonic()
