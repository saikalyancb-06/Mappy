"""TTL cache backed by the database, with an in-process front.

Entries always carry created_at/expires_at so cached dynamic data (weather,
web results) is never presented as current once it expires.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any

from app.db.models import CacheEntry, utcnow
from app.db.session import SessionLocal

_memory: dict[str, tuple[datetime, dict[str, Any]]] = {}
_lock = threading.Lock()


def cache_get(namespace: str, key: str) -> dict[str, Any] | None:
    """Return {'data', 'source', 'created_at', 'expires_at'} or None when missing/expired."""
    full_key = f"{namespace}:{key}"
    now = utcnow()
    with _lock:
        hit = _memory.get(full_key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        with SessionLocal() as db:
            row = db.get(CacheEntry, full_key)
            if row is None or row.expires_at <= now:
                return None
            entry = {"data": json.loads(row.data), "source": row.source, "created_at": row.created_at.isoformat() + "Z", "expires_at": row.expires_at.isoformat() + "Z"}
    except Exception:
        return None
    with _lock:
        _memory[full_key] = (row.expires_at, entry)
    return entry


def cache_set(namespace: str, key: str, data: Any, ttl_seconds: int, source: str | None = None) -> dict[str, Any]:
    full_key = f"{namespace}:{key}"
    created = utcnow()
    expires = created + timedelta(seconds=ttl_seconds)
    entry = {"data": data, "source": source, "created_at": created.isoformat() + "Z", "expires_at": expires.isoformat() + "Z"}
    with _lock:
        _memory[full_key] = (expires, entry)
    try:
        with SessionLocal() as db:
            row = db.get(CacheEntry, full_key)
            payload = json.dumps(data, default=str)
            if row is None:
                db.add(CacheEntry(key=full_key, namespace=namespace, data=payload, source=source, created_at=created, expires_at=expires))
            else:
                row.data, row.source, row.created_at, row.expires_at = payload, source, created, expires
            db.commit()
    except Exception:
        pass  # the in-memory entry still serves this process
    return entry


def cache_clear_memory() -> None:
    with _lock:
        _memory.clear()
