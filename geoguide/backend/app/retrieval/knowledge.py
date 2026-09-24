"""Hybrid knowledge retrieval: metadata filter → BM25 + vector → RRF fusion.

Structured questions (distance, category, hours) never come here; this is only
for semantic knowledge such as history, culture, etiquette and descriptions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select

from app.core.text import normalize
from app.db.models import EntityAlias, KnowledgeChunk
from app.db.session import SessionLocal
from app.retrieval import vector_store
from app.retrieval.embeddings import EmbeddingProvider, EmbeddingUnavailable, provider as default_provider
from app.retrieval.lexical import bm25_scores
from app.retrieval.vector_store import ChunkFilter

logger = logging.getLogger(__name__)

RRF_K = 60
MIN_RELATIVE_BM25 = 0.3  # lexical-only hits weaker than this fraction of the best are dropped
MIN_VECTOR_SIMILARITY = 0.30  # below this a vector-only hit is treated as irrelevant


@dataclass
class KnowledgeHit:
    chunk_id: str
    title: str | None
    content: str
    kind: str
    destination_id: str | None
    poi_id: str | None
    category: str | None
    source: str | None
    source_url: str | None
    confidence: float | None
    updated_at: str | None
    scores: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class KnowledgeResult:
    hits: list[KnowledgeHit]
    semantic_status: str  # ok | unavailable | no_embeddings
    semantic_reason: str | None
    lexical_matches: int
    vector_matches: int


def retrieve(
    query: str,
    *,
    destination_id: str | None = None,
    poi_ids: Iterable[str] | None = None,
    kinds: Iterable[str] | None = None,
    limit: int = 6,
    embedder: EmbeddingProvider | None = None,
) -> KnowledgeResult:
    embedder = embedder or default_provider
    flt = ChunkFilter(destination_id=destination_id, poi_ids=list(poi_ids or []) or None, kinds=list(kinds or []) or None)
    with SessionLocal() as db:
        statement = vector_store._apply_filter(select(KnowledgeChunk), flt)
        chunks = {chunk.id: chunk for chunk in db.scalars(statement).all()}
        aliases = db.scalars(select(EntityAlias.normalized).where(EntityAlias.entity_type == "destination", EntityAlias.entity_id == destination_id)).all() if destination_id else []
    if not chunks:
        return KnowledgeResult([], "ok", None, 0, 0)

    # Inside a destination-filtered corpus the destination's own name carries no signal.
    lexical_query = normalize(query)
    for alias in sorted(aliases, key=len, reverse=True):
        stripped = f" {lexical_query} ".replace(f" {alias} ", " ").strip()
        if stripped:
            lexical_query = stripped
    lexical = bm25_scores(lexical_query, [(chunk.id, f"{chunk.title or ''} {chunk.content}") for chunk in chunks.values()])

    vector: dict[str, float] = {}
    semantic_status, semantic_reason = "ok", None
    try:
        query_vector = embedder.embed([query], wait=False)[0]
        vector = {chunk_id: sim for chunk_id, sim in vector_store.search(query_vector, embedder.model_name, flt, limit=max(limit * 4, 20)) if chunk_id in chunks}
        if not vector and not any(chunk.embedding_model == embedder.model_name for chunk in chunks.values()):
            semantic_status, semantic_reason = "no_embeddings", "knowledge has not been embedded with the current model yet"
    except EmbeddingUnavailable as exc:
        semantic_status, semantic_reason = "unavailable", str(exc)
    except Exception as exc:  # never fail a request because of the vector index
        semantic_status, semantic_reason = "unavailable", f"{type(exc).__name__}"
        logger.warning("vector_search_failed error=%s", exc)

    if lexical:
        best = max(lexical.values())
        lexical = {chunk_id: score for chunk_id, score in lexical.items() if score >= MIN_RELATIVE_BM25 * best or chunk_id in vector}
    poi_focus = set(flt.poi_ids or [])
    lexical_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(sorted(lexical.items(), key=lambda item: item[1], reverse=True), start=1)}
    relevant_vector = {chunk_id: sim for chunk_id, sim in vector.items() if sim >= MIN_VECTOR_SIMILARITY}
    vector_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(sorted(relevant_vector.items(), key=lambda item: item[1], reverse=True), start=1)}

    fused: dict[str, float] = {}
    for chunk_id in set(lexical_rank) | set(vector_rank):
        score = 0.0
        if chunk_id in lexical_rank:
            score += 1.0 / (RRF_K + lexical_rank[chunk_id])
        if chunk_id in vector_rank:
            score += 1.0 / (RRF_K + vector_rank[chunk_id])
        if poi_focus and chunks[chunk_id].poi_id in poi_focus:
            score += 1.0 / (RRF_K + 1)  # the resolved entity's own facts come first
        fused[chunk_id] = score
    # A resolved entity's facts are relevant even when wording differs.
    for chunk_id, chunk in chunks.items():
        if poi_focus and chunk.poi_id in poi_focus and chunk_id not in fused:
            fused[chunk_id] = 1.0 / (RRF_K + 10)

    ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)[:limit]
    hits = []
    for chunk_id, score in ordered:
        chunk = chunks[chunk_id]
        hits.append(KnowledgeHit(
            chunk_id=chunk.id,
            title=chunk.title,
            content=chunk.content,
            kind=chunk.kind,
            destination_id=chunk.destination_id,
            poi_id=chunk.poi_id,
            category=chunk.category,
            source=chunk.source,
            source_url=chunk.source_url,
            confidence=chunk.confidence,
            updated_at=chunk.updated_at.isoformat() + "Z" if chunk.updated_at else None,
            scores={"rrf": round(score, 5), "bm25": round(lexical.get(chunk_id, 0.0), 4), "vector": round(vector.get(chunk_id, 0.0), 4)},
        ))
    return KnowledgeResult(hits, semantic_status, semantic_reason, len(lexical), len(relevant_vector))
