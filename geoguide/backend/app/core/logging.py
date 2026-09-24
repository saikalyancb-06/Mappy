"""Structured logging and per-request retrieval traces."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.config import LOG_LEVEL

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _CONFIGURED = True


trace_logger = logging.getLogger("geoguide.trace")


class Trace:
    """Collects every retrieval decision made while answering one request.

    The trace is logged as one JSON line and can be returned to the client in
    debug mode, so "why did GeoGuide return this place?" is answerable without
    reading the backend.
    """

    def __init__(self, kind: str, query: str | None = None) -> None:
        self.request_id = uuid.uuid4().hex
        self.kind = kind
        self.query = query
        self.started = time.monotonic()
        self.steps: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {}
        self.errors: list[dict[str, Any]] = []
        self._timers: dict[str, float] = {}

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def step(self, name: str, **details: Any) -> None:
        self.steps.append({"step": name, "t_ms": self.elapsed_ms(), **details})

    def error(self, source: str, code: str, message: str) -> None:
        self.errors.append({"source": source, "code": code, "message": message})

    def start_timer(self, name: str) -> None:
        self._timers[name] = time.monotonic()

    def stop_timer(self, name: str) -> int:
        started = self._timers.pop(name, None)
        elapsed = int((time.monotonic() - started) * 1000) if started else 0
        self.data.setdefault("latency_ms", {})[name] = elapsed
        return elapsed

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "query": self.query,
            "total_ms": self.elapsed_ms(),
            "steps": self.steps,
            "errors": self.errors,
            **self.data,
        }

    def emit(self) -> None:
        try:
            trace_logger.info(json.dumps(self.as_dict(), default=str)[:20000])
        except Exception:  # pragma: no cover - logging must never break a request
            trace_logger.info("trace_emit_failed request_id=%s", self.request_id)
