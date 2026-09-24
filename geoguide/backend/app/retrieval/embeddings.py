"""Semantic embedding provider (sentence-transformers).

If the model cannot be loaded (package missing, no network to download
weights), semantic retrieval is reported as *unavailable* and retrieval falls
back to lexical BM25. Nothing pretends to be an embedding when it is not.
"""
from __future__ import annotations

import logging
import threading
import time

from app.config import EMBEDDING_MODEL, EMBEDDINGS_ENABLED

logger = logging.getLogger(__name__)
_RETRY_AFTER_S = 600


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingProvider:
    def __init__(self, model_name: str | None = None, enabled: bool | None = None) -> None:
        self.model_name = model_name or EMBEDDING_MODEL
        self.enabled = EMBEDDINGS_ENABLED if enabled is None else enabled
        self._model = None
        self._failed_at: float | None = None
        self.reason: str | None = None if self.enabled else "disabled by EMBEDDINGS_ENABLED"
        self._lock = threading.Lock()

    @property
    def dim(self) -> int | None:
        if self._model is None:
            return None
        return int(self._model.get_sentence_embedding_dimension())

    def _load(self, wait: bool = True):
        if not self.enabled:
            raise EmbeddingUnavailable(self.reason or "disabled")
        if self._model is not None:
            return self._model
        if self._failed_at and time.monotonic() - self._failed_at < _RETRY_AFTER_S:
            raise EmbeddingUnavailable(self.reason or "model unavailable")
        if not wait:
            # Request paths never block on a model download: load in the background.
            self.load_in_background()
            raise EmbeddingUnavailable("embedding model is loading")
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer  # heavy import, done lazily

                self._model = SentenceTransformer(self.model_name)
                self.reason = None
                logger.info("embedding_model_loaded model=%s", self.model_name)
                return self._model
            except Exception as exc:  # missing package, download failure, incompatible binary
                self._failed_at = time.monotonic()
                self.reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("embedding_model_unavailable model=%s reason=%s", self.model_name, self.reason)
                raise EmbeddingUnavailable(self.reason) from None

    def load_in_background(self) -> None:
        if self._model is not None or self._lock.locked() or not self.enabled:
            return
        threading.Thread(target=self._safe_load, name="geoguide-embedding-load", daemon=True).start()

    def _safe_load(self) -> None:
        try:
            self._load(wait=True)
        except EmbeddingUnavailable:
            pass

    def available(self) -> bool:
        try:
            self._load()
            return True
        except EmbeddingUnavailable:
            return False

    def embed(self, texts: list[str], wait: bool = True) -> list[list[float]]:
        model = self._load(wait=wait)
        vectors = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return [list(map(float, vector)) for vector in vectors]

    def status(self) -> dict[str, object]:
        return {"model": self.model_name, "enabled": self.enabled, "loaded": self._model is not None, "dim": self.dim, "reason": self.reason}


provider = EmbeddingProvider()
