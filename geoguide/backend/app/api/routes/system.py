from __future__ import annotations

from fastapi import APIRouter

from app.config import APP_ENV
from app.core.rules import load_rules, taxonomy
from app.db.models import Destination, KnowledgeChunk, Poi
from app.db.session import CAPABILITIES, SessionLocal
from app.llm.client import llm
from app.retrieval.embeddings import provider as embeddings
from app.retrieval.vector_store import embedding_coverage
from app.search.serpapi import client as serp_client

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    with SessionLocal() as db:
        counts = {"destinations": db.query(Destination).count(), "pois": db.query(Poi).count(), "knowledge_chunks": db.query(KnowledgeChunk).count()}
    return {
        "status": "ok",
        "environment": APP_ENV,
        "services": {
            "database": {"dialect": CAPABILITIES["dialect"], "postgis": CAPABILITIES["postgis"], "pgvector": CAPABILITIES["pgvector"], **counts},
            "embeddings": {**embeddings.status(), **embedding_coverage(embeddings.model_name)},
            "llm": {"provider": "groq", **llm.status()},
            "web_search": {"provider": "serpapi", "configured": serp_client.configured},
            "weather": {"provider": "open-meteo"},
        },
    }


@router.get("/config")
def client_config() -> dict:
    """Taxonomy and option lists for the frontend, so the UI never hardcodes them."""
    tax = taxonomy()
    itinerary = load_rules("itinerary")
    return {
        "interests": [{"id": key, "label": value["label"]} for key, value in tax["groups"].items() if key not in {"stay", "services"}],
        "categories": [{"id": key, "label": value["label"], "kind": value["kind"], "group": value.get("group")} for key, value in tax["categories"].items()],
        "groups": [{"id": key, "label": value["label"], "categories": value["categories"]} for key, value in tax["groups"].items()],
        "accessibility": [{"id": "step_free", "label": "Step-free access"}],
        "languages": [{"id": "en", "label": "English"}, {"id": "kn", "label": "ಕನ್ನಡ Kannada"}, {"id": "hi", "label": "हिन्दी Hindi"}],
        "budgets": ["low", "moderate", "high"],
        "paces": ["relaxed", "balanced", "packed"],
        "walking": ["low", "moderate", "high"],
        "plan_durations": list(itinerary["durations_min"].keys()),
        "plan_presets": list(itinerary["presets"].keys()),
        "travel_modes": [{"id": "walk", "label": "Walk"}, {"id": "bicycle", "label": "Cycle"}, {"id": "motorbike", "label": "Bike / scooter"}, {"id": "car", "label": "Car / cab"}, {"id": "auto", "label": "Auto"}, {"id": "transit", "label": "Bus / metro"}],
        "ranking_modes": [{"id": "popular", "label": "Popular"}, {"id": "local", "label": "Local favourites"}, {"id": "hidden_gems", "label": "Hidden gems"}],
        "hotel_sorts": [{"id": "best", "label": "Best match"}, {"id": "cheapest", "label": "Cheapest"}, {"id": "nearest", "label": "Nearest"}, {"id": "top_rated", "label": "Top rated"}],
        "currencies": ["INR", "USD", "EUR", "GBP", "AED", "THB", "SGD", "JPY", "LKR", "NPR"],
    }
