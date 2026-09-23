from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

try:
    from qdrant_client import QdrantClient
except ImportError:  # pragma: no cover - optional dependency in early scaffold stage
    QdrantClient = None  # type: ignore[assignment]

from app.config import QDRANT_COLLECTION_NAME, QDRANT_PATH, QDRANT_URL


class QdrantService:
    def __init__(self, url: str | None = None, path: str | None = None, collection_name: str | None = None) -> None:
        self.url = url or QDRANT_URL
        self.path = path or QDRANT_PATH
        self.collection_name = collection_name or QDRANT_COLLECTION_NAME
        self.client = self._build_client()

    def _build_client(self):
        if QdrantClient is None:
            return None
        if self.url.startswith('http'):
            return QdrantClient(url=self.url)
        path = Path(self.path)
        path.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(path))

    def ensure_collection(self, vector_size: int = 384) -> None:
        if self.client is None:
            return None
        try:
            self.client.get_collection(self.collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={'size': vector_size, 'distance': 'Cosine'},
            )

    def upsert(self, vector: list[float], payload: dict[str, Any]) -> str:
        if self.client is None:
            return payload.get('id') or str(uuid.uuid4())
        point_id = payload.get('id') or str(uuid.uuid4())
        self.client.upsert(
            collection_name=self.collection_name,
            points=[{
                'id': point_id,
                'vector': vector,
                'payload': payload,
            }],
        )
        return point_id

    def search(self, query_vector: list[float], limit: int = 5, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.client is None:
            return []
        return self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit,
            query_filter=filters,
        )

    def health_check(self) -> dict[str, Any]:
        if self.client is None:
            return {'status': 'degraded', 'collection': self.collection_name, 'reason': 'qdrant-client is not installed in this environment.'}
        try:
            self.ensure_collection()
            return {'status': 'ok', 'collection': self.collection_name}
        except Exception as exc:  # pragma: no cover - runtime only path
            return {'status': 'error', 'reason': str(exc)}
