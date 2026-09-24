from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_ENV = BASE_DIR.parent.parent / ".env"
load_dotenv(ROOT_ENV)
load_dotenv(BASE_DIR / ".env", override=True)

APP_ENV = os.getenv("APP_ENV", "development")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/geoguide.db")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL_FAST = os.getenv("GROQ_MODEL_FAST", "qwen/qwen3.8-27b")
GROQ_MODEL_REASONING = os.getenv("GROQ_MODEL_REASONING", "qwen/qwen3.8-27b")
GROQ_MODEL_RESEARCH = os.getenv("GROQ_MODEL_RESEARCH", "qwen/qwen3.8-27b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
QDRANT_URL = os.getenv("QDRANT_URL", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
QDRANT_PATH = os.getenv("QDRANT_PATH", str(BASE_DIR / "data" / "qdrant"))
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "place_knowledge")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
AUTH_SECRET = os.getenv("AUTH_SECRET", "development-only-change-this-secret")
