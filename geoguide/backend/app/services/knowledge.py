from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.rag.embeddings import EmbeddingService
from app.rag.qdrant_store import QdrantService
from app.db.models import KnowledgeDocument
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(self, embeddings: EmbeddingService | None = None, store: QdrantService | None = None) -> None:
        self.embeddings = embeddings or EmbeddingService()
        self.store = store or QdrantService()

    def index_places(self, places: list[dict[str, Any]], area_id: str) -> int:
        if not places:
            return 0
        texts = [self._place_text(place) for place in places]
        with SessionLocal() as db:
            for place, text in zip(places, texts):
                document_id = f'{area_id}:{place.get("id")}'
                document = db.get(KnowledgeDocument, document_id)
                if document is None:
                    db.add(KnowledgeDocument(id=document_id, area_id=area_id, entity_id=str(place.get('id') or ''), content=text, source='OpenStreetMap'))
                else:
                    document.content = text
            db.commit()
        try:
            vectors = self.embeddings.embed_many(texts)
            if not vectors:
                return 0
            self.store.ensure_collection(len(vectors[0]))
            for place, vector, text in zip(places, vectors, texts):
                self.store.upsert(vector, {
                    'id': f"poi:{area_id}:{place.get('id')}",
                    'entity_id': place.get('id'),
                    'area_id': area_id,
                    'category': place.get('category'),
                    'source': 'OpenStreetMap',
                    'text': text,
                })
            return len(vectors)
        except Exception as exc:
            logger.warning('knowledge_index_failed area_id=%s error_type=%s', area_id, type(exc).__name__)
            return 0

    def retrieve(self, query: str, area_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        lexical = self._lexical_retrieve(query, area_id, limit)
        try:
            vector = self.embeddings.embed(query)
            self.store.ensure_collection(len(vector))
            results = self.store.search(vector, limit=limit * 5)
            evidence = []
            for result in results:
                payload = getattr(result, 'payload', None) if not isinstance(result, dict) else result.get('payload', result)
                if not isinstance(payload, dict):
                    continue
                if area_id and payload.get('area_id') != area_id:
                    continue
                evidence.append(payload)
            return evidence[:limit] or lexical
        except Exception as exc:
            logger.info('knowledge_retrieval_unavailable error_type=%s', type(exc).__name__)
            return lexical

    @staticmethod
    def _lexical_retrieve(query: str, area_id: str | None, limit: int) -> list[dict[str, Any]]:
        terms = set(re.findall(r'[a-z0-9]+', query.casefold()))
        if not terms:
            return []
        with SessionLocal() as db:
            documents = db.query(KnowledgeDocument).filter(KnowledgeDocument.area_id == area_id).all() if area_id else db.query(KnowledgeDocument).all()
            ranked = []
            for document in documents:
                score = len(terms.intersection(set(re.findall(r'[a-z0-9]+', document.content.casefold()))))
                if score:
                    ranked.append((score, document))
            ranked.sort(key=lambda item: item[0], reverse=True)
            return [{'entity_id': document.entity_id, 'area_id': document.area_id, 'text': document.content, 'source': document.source, 'score': score} for score, document in ranked[:limit]]

    @staticmethod
    def _place_text(place: dict[str, Any]) -> str:
        return '. '.join(str(value) for value in (place.get('name'), place.get('category'), place.get('address'), place.get('opening_hours')) if value)
