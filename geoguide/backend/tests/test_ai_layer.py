from app.llm.client import LLMClient
from app.rag.embeddings import EmbeddingService
from app.rag.qdrant_store import QdrantService


def test_llm_client_is_configured() -> None:
    client = LLMClient(api_key='demo-key', base_url='https://api.groq.com/openai/v1')
    assert client.base_url == 'https://api.groq.com/openai/v1'
    assert client.api_key == 'demo-key'


def test_embedding_service_can_load_model() -> None:
    service = EmbeddingService(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    assert service.model_name.endswith('MiniLM-L12-v2')


def test_qdrant_service_has_collection_name() -> None:
    service = QdrantService(url='http://localhost:6333', path='./data/qdrant', collection_name='place_knowledge')
    assert service.collection_name == 'place_knowledge'
