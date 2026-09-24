"""Server-side configuration. Every secret is read from the environment only."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_ENV = BASE_DIR.parent.parent / ".env"
load_dotenv(ROOT_ENV)
load_dotenv(BASE_DIR / ".env", override=True)


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "development")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", str(DATA_DIR / "config")))
PACKS_DIR = Path(os.getenv("PACKS_DIR", str(DATA_DIR / "packs")))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'geoguide.db'}")
# Load every data pack found in PACKS_DIR on startup when the tables are empty.
AUTO_SEED_PACKS = _bool("AUTO_SEED_PACKS", True)

# LLM (Groq, OpenAI-compatible)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
GROQ_MODEL_REASONING = os.getenv("GROQ_MODEL_REASONING", "llama-3.3-70b-versatile")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "25"))
# Use the LLM to refine query understanding when the rule parser is unsure.
LLM_QUERY_PARSING = _bool("LLM_QUERY_PARSING", True)

# Web search (SerpApi)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPAPI_TIMEOUT_SECONDS = float(os.getenv("SERPAPI_TIMEOUT_SECONDS", "8"))

# Semantic embeddings
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDINGS_ENABLED = _bool("EMBEDDINGS_ENABLED", True)

# External providers
NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
HTTP_USER_AGENT = os.getenv("HTTP_USER_AGENT", "GeoGuide/0.2 (location-aware travel companion)")

# Location freshness (seconds)
LOCATION_SOFT_MAX_AGE_S = int(os.getenv("LOCATION_SOFT_MAX_AGE_S", "600"))
LOCATION_HARD_MAX_AGE_S = int(os.getenv("LOCATION_HARD_MAX_AGE_S", "3600"))
LOCATION_LOW_ACCURACY_M = float(os.getenv("LOCATION_LOW_ACCURACY_M", "500"))

# Cache TTLs (seconds)
CACHE_TTL_GEOCODE_S = int(os.getenv("CACHE_TTL_GEOCODE_S", str(7 * 24 * 3600)))
CACHE_TTL_WEATHER_S = int(os.getenv("CACHE_TTL_WEATHER_S", "900"))
CACHE_TTL_WEB_S = int(os.getenv("CACHE_TTL_WEB_S", "1800"))
CACHE_TTL_PACK_S = int(os.getenv("CACHE_TTL_PACK_S", "600"))

AUTH_SECRET = os.getenv("AUTH_SECRET", "development-only-change-this-secret")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
