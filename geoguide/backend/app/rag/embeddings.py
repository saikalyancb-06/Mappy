from __future__ import annotations

from typing import Any

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency in early scaffold stage
    SentenceTransformer = None  # type: ignore[assignment]

from app.config import EMBEDDING_MODEL


class EmbeddingService:
    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or EMBEDDING_MODEL
        self.model = None

    def ensure_loaded(self) -> None:
        if SentenceTransformer is None:
            raise RuntimeError('sentence-transformers is not installed in this environment.')
        if self.model is None:
            self.model = SentenceTransformer(self.model_name)

    def embed(self, text: str) -> list[float]:
        self.ensure_loaded()
        vector = self.model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.ensure_loaded()
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()

    def health_check(self) -> dict[str, Any]:
        try:
            self.ensure_loaded()
            return {'status': 'ok', 'model': self.model_name}
        except Exception as exc:  # pragma: no cover - runtime-only path
            return {'status': 'error', 'reason': str(exc)}
