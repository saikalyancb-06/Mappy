"""Hybrid retrieval, embeddings consistency, weather and safety context."""
from datetime import date

from app.knowledge.context import active_advisories, events_for, weather_notices
from app.retrieval import vector_store
from app.retrieval.embeddings import EmbeddingProvider, EmbeddingUnavailable
from app.retrieval.indexer import reindex
from app.retrieval.knowledge import retrieve
from app.weather.open_meteo import get_weather


class FakeEmbedder(EmbeddingProvider):
    """Deterministic bag-of-concepts vectors, standing in for a real model in tests."""

    CONCEPTS = ["history", "fort", "temple", "shoes", "food", "cafe", "river", "calm"]
    SYNONYMS = {"past": "history", "founded": "history", "footwear": "shoes", "quiet": "calm", "uncrowded": "calm", "eat": "food", "thali": "food"}

    def __init__(self, name="fake-model"):
        super().__init__(model_name=name, enabled=True)

    def embed(self, texts, wait=True):
        vectors = []
        for text in texts:
            words = [self.SYNONYMS.get(w, w) for w in text.lower().replace(".", " ").split()]
            vectors.append([float(sum(1 for w in words if w.startswith(c))) + 0.01 for c in self.CONCEPTS])
        return vectors


def test_lexical_retrieval_is_destination_scoped():
    hits = retrieve("when was the fort built history", destination_id="dest-testville").hits
    assert hits and hits[0].chunk_id.startswith("kb-tv-history")
    assert all(hit.destination_id == "dest-testville" for hit in hits)
    assert retrieve("when was the fort built", destination_id="dest-elsewhere").hits == []


def test_destination_name_alone_does_not_match_everything():
    from sqlalchemy import func, select

    from app.db.models import KnowledgeChunk
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.destination_id == "dest-testville"))
    hits = retrieve("Testville", destination_id="dest-testville").hits
    assert len(hits) <= 3 and len(hits) < total / 2


def test_no_relevant_knowledge_returns_nothing():
    assert retrieve("quantum chromodynamics lecture", destination_id="dest-testville").hits == []


def test_semantic_unavailable_is_reported_not_faked():
    class Broken(EmbeddingProvider):
        def embed(self, texts, wait=True):
            raise EmbeddingUnavailable("model missing")

    result = retrieve("temple shoes", destination_id="dest-testville", embedder=Broken(enabled=True))
    assert result.semantic_status == "unavailable" and result.hits  # lexical still answers


def test_vector_path_finds_paraphrases_and_never_mixes_models():
    embedder = FakeEmbedder()
    assert reindex(force=True, embedder=embedder)["status"] == "ok"
    result = retrieve("is it quiet and uncrowded", destination_id="dest-testville", embedder=embedder)
    assert result.semantic_status == "ok"
    assert any(hit.chunk_id == "fact-tv-temple-1" for hit in result.hits)
    # A different model's query vector must not be compared with these embeddings.
    assert vector_store.search(embedder.embed(["calm"])[0], "another-model", vector_store.ChunkFilter()) == []
    coverage = vector_store.embedding_coverage("fake-model")
    assert coverage["embedded_with_current_model"] == coverage["chunks"]


def test_entity_facts_are_prioritised_when_resolved():
    hits = retrieve("tell me about it", destination_id="dest-testville", poi_ids=["tv-old-fort"]).hits
    assert hits and hits[0].poi_id == "tv-old-fort"


def test_weather_current_and_forecast(weather_ok):
    weather_ok(apparent_max=39.0, precipitation=70, code=63)
    today = get_weather(10.0, 20.0, 0)
    assert today["status"] == "ok" and today["current"]["summary"] == "Rain"
    assert set(today["signals"]) == {"heat", "rain"}
    tomorrow = get_weather(10.0, 20.0, 1)
    assert tomorrow["current"] is None and tomorrow["day"]["date"] != today["day"]["date"]


def test_weather_failure_is_explicit():
    weather = get_weather(-40.0, -40.0, 0)  # network disabled in tests; no dataset record here either
    assert weather["status"] == "unavailable" and weather["error"]["source"] == "open_meteo"
    assert weather_notices(weather) == []


def test_advisories_respect_season_and_keep_severity():
    july = active_advisories("dest-testville", date(2026, 7, 1))
    december = active_advisories("dest-testville", date(2026, 12, 1))
    assert [a["id"] for a in july] == ["adv-tv-river"]
    assert {a["id"] for a in december} == {"adv-tv-river", "adv-tv-winter"}
    assert july[0]["severity"] == "high"
    assert active_advisories("dest-nowhere", date(2026, 7, 1)) == []


def test_events_distinguish_dated_and_recurring():
    events = events_for("dest-testville", date(2026, 7, 1))
    by_id = {e["id"]: e for e in events}
    assert by_id["evt-tv-fair"]["timing"] == "usually_this_time_of_year"
    assert "evt-tv-dated" not in by_id  # a dated event in the past is never presented as upcoming
    around = {e["id"]: e for e in events_for("dest-testville", date(2000, 12, 30), days=7)}
    assert around["evt-tv-dated"]["timing"] == "dated"
