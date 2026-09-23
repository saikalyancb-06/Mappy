from app.llm.client import LLMClient
from app.rag.embeddings import EmbeddingService
from app.rag import qdrant_store
from app.rag.qdrant_store import QdrantService


def test_llm_client_reports_unconfigured_without_key():
    client = LLMClient(api_key='')
    result = client.health_check()
    assert result['status'] == 'unconfigured'


def test_embedding_health_check_uses_dummy_model(monkeypatch):
    class DummyModel:
        def encode(self, *args, **kwargs):
            return [0.1, 0.2, 0.3]

    monkeypatch.setattr('app.rag.embeddings.SentenceTransformer', lambda *args, **kwargs: DummyModel())
    service = EmbeddingService(model_name='dummy-model')
    result = service.health_check()
    assert result['status'] == 'ok'
    assert result['model'] == 'dummy-model'


def test_qdrant_health_check_handles_missing_server(monkeypatch):
    class DummyClient:
        def __init__(self, *args, **kwargs):
            self.created = True

        def get_collection(self, collection_name):
            raise RuntimeError('collection missing')

        def create_collection(self, *args, **kwargs):
            return None

    monkeypatch.setattr(qdrant_store, 'QdrantClient', DummyClient)
    service = QdrantService(url='local', path='D:/kognivera/geoguide/backend/data/qdrant', collection_name='smoke')
    result = service.health_check()
    assert result['status'] == 'ok'
