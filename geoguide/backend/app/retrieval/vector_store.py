"""Vector storage and similarity search over ``knowledge_chunks``.

Postgres + pgvector: cosine distance in SQL on ``embedding_vec``.
SQLite: vectors stored as JSON; cosine computed with numpy over the
metadata-filtered subset. Only vectors produced by the *same* embedding model
are ever compared.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from sqlalchemy import select, text

from app.db.models import KnowledgeChunk, utcnow
from app.db.session import CAPABILITIES, SessionLocal


@dataclass
class ChunkFilter:
    destination_id: str | None = None
    poi_ids: Iterable[str] | None = None
    kinds: Iterable[str] | None = None
    include_destination_level: bool = True  # with poi_ids, also keep destination-wide chunks


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"


def store_embeddings(items: list[tuple[str, list[float]]], model_name: str) -> int:
    if not items:
        return 0
    with SessionLocal() as db:
        for chunk_id, vector in items:
            chunk = db.get(KnowledgeChunk, chunk_id)
            if chunk is None:
                continue
            chunk.embedding = json.dumps(vector)
            chunk.embedding_model = model_name
            chunk.embedding_dim = len(vector)
            chunk.updated_at = chunk.updated_at or utcnow()
        db.commit()
        if CAPABILITIES.get("pgvector"):
            for chunk_id, vector in items:
                db.execute(text("UPDATE knowledge_chunks SET embedding_vec = CAST(:v AS vector) WHERE id = :id"), {"v": _vector_literal(vector), "id": chunk_id})
            db.commit()
    return len(items)


def _apply_filter(statement, flt: ChunkFilter):
    if flt.destination_id:
        statement = statement.where(KnowledgeChunk.destination_id == flt.destination_id)
    poi_ids = list(flt.poi_ids or [])
    if poi_ids:
        if flt.include_destination_level:
            statement = statement.where((KnowledgeChunk.poi_id.in_(poi_ids)) | (KnowledgeChunk.poi_id.is_(None)))
        else:
            statement = statement.where(KnowledgeChunk.poi_id.in_(poi_ids))
    kinds = list(flt.kinds or [])
    if kinds:
        statement = statement.where(KnowledgeChunk.kind.in_(kinds))
    return statement


def search(query_vector: list[float], model_name: str, flt: ChunkFilter, limit: int = 20) -> list[tuple[str, float]]:
    """Return [(chunk_id, cosine_similarity)] best first."""
    with SessionLocal() as db:
        if CAPABILITIES.get("pgvector"):
            conditions = ["embedding_model = :model", "embedding_vec IS NOT NULL"]
            params: dict = {"q": _vector_literal(query_vector), "model": model_name, "limit": limit}
            if flt.destination_id:
                conditions.append("destination_id = :destination_id")
                params["destination_id"] = flt.destination_id
            poi_ids = list(flt.poi_ids or [])
            if poi_ids:
                conditions.append("(poi_id = ANY(:poi_ids)" + (" OR poi_id IS NULL)" if flt.include_destination_level else ")"))
                params["poi_ids"] = poi_ids
            kinds = list(flt.kinds or [])
            if kinds:
                conditions.append("kind = ANY(:kinds)")
                params["kinds"] = kinds
            rows = db.execute(text(f"SELECT id, 1 - (embedding_vec <=> CAST(:q AS vector)) AS similarity FROM knowledge_chunks WHERE {' AND '.join(conditions)} ORDER BY embedding_vec <=> CAST(:q AS vector) LIMIT :limit"), params).all()
            return [(row.id, float(row.similarity)) for row in rows]

        statement = _apply_filter(select(KnowledgeChunk.id, KnowledgeChunk.embedding).where(KnowledgeChunk.embedding_model == model_name, KnowledgeChunk.embedding.is_not(None)), flt)
        rows = db.execute(statement).all()
    if not rows:
        return []
    ids = [row.id for row in rows]
    matrix = np.array([json.loads(row.embedding) for row in rows], dtype=np.float32)
    query = np.array(query_vector, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(query) or 1.0)
    similarities = (matrix @ query) / np.where(norms == 0, 1.0, norms)
    order = np.argsort(-similarities)[:limit]
    return [(ids[i], float(similarities[i])) for i in order]


def embedding_coverage(model_name: str) -> dict[str, int]:
    with SessionLocal() as db:
        total = db.query(KnowledgeChunk).count()
        current = db.query(KnowledgeChunk).filter(KnowledgeChunk.embedding_model == model_name).count()
    return {"chunks": total, "embedded_with_current_model": current}
