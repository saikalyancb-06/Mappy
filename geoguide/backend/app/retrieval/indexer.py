"""(Re)index knowledge chunks with the configured embedding model.

Run manually with ``python -m app.retrieval.indexer [--force]``; the API also
runs it in the background at startup. Chunks embedded with another model are
re-embedded so vectors from incompatible models are never mixed.
"""
from __future__ import annotations

import argparse
import logging
import threading

from sqlalchemy import or_, select

from app.db.models import KnowledgeChunk
from app.db.session import SessionLocal, init_db
from app.retrieval import vector_store
from app.retrieval.embeddings import EmbeddingProvider, EmbeddingUnavailable, provider as default_provider

logger = logging.getLogger(__name__)
_running = threading.Lock()


def reindex(force: bool = False, embedder: EmbeddingProvider | None = None, batch_size: int = 64) -> dict[str, object]:
    embedder = embedder or default_provider
    if not _running.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        with SessionLocal() as db:
            statement = select(KnowledgeChunk.id, KnowledgeChunk.title, KnowledgeChunk.content)
            if not force:
                statement = statement.where(or_(KnowledgeChunk.embedding.is_(None), KnowledgeChunk.embedding_model != embedder.model_name, KnowledgeChunk.embedding_model.is_(None)))
            pending = db.execute(statement).all()
        if not pending:
            return {"status": "up_to_date", "embedded": 0, "model": embedder.model_name}
        embedded = 0
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = embedder.embed([f"{row.title or ''}. {row.content}" for row in batch])
            embedded += vector_store.store_embeddings([(row.id, vector) for row, vector in zip(batch, vectors)], embedder.model_name)
        logger.info("knowledge_reindexed model=%s chunks=%d", embedder.model_name, embedded)
        return {"status": "ok", "embedded": embedded, "model": embedder.model_name}
    except EmbeddingUnavailable as exc:
        return {"status": "embeddings_unavailable", "reason": str(exc), "model": embedder.model_name}
    finally:
        _running.release()


def reindex_in_background() -> None:
    threading.Thread(target=reindex, name="geoguide-reindex", daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embed knowledge chunks with the configured model")
    parser.add_argument("--force", action="store_true", help="re-embed every chunk")
    args = parser.parse_args()
    init_db()
    print(reindex(force=args.force))
