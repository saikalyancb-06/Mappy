# GeoGuide

GeoGuide is a location-aware AI place companion built to work for any place on Earth using live data fetched at runtime.

## Repository layout

- backend/ — FastAPI service, SQLite schema, ingestion, LLM/RAG components
- frontend/ — Vite + React app
- README.md — setup and run instructions

## Quick start

1. Create a Python environment.
2. Install backend dependencies:

   ```bash
   cd geoguide/backend
   python -m venv .venv
   . .venv/bin/activate  # Windows: .venv\Scripts\activate
   python -m pip install -r requirements.txt
   cp .env.example .env
   ```

3. Start the backend:

   ```bash
   cd geoguide/backend
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. Check the health endpoint:

   ```bash
   curl http://localhost:8000/api/health
   ```

5. Start the mobile-first frontend in a second terminal:

   ```bash
   cd geoguide/frontend
   npm install
   npm run dev
   ```

   Open the printed Vite URL. On a desktop browser, GeoGuide intentionally stays inside a centered phone-width frame. The first run shows onboarding and a location permission step; preferences and the permission choice are stored locally for that browser.

## Environment

Set values in backend/.env before running the app. At minimum:

- Use Python 3.12 for the backend environment. The project avoids Python 3.14 because of dependency compatibility issues in the FastAPI/Pydantic stack.
- Keep a single backend virtual environment under geoguide/backend/.venv rather than duplicating venvs across the repo.

- GROQ_API_KEY
- GROQ_MODEL_FAST
- GROQ_MODEL_REASONING
- GROQ_MODEL_RESEARCH
- AUTH_SECRET (use a long random value outside development)

The project is intentionally configured to use environment variables for keys and model selection.

## Resilience defaults

- API endpoints accept missing or invalid location payloads and fall back to a safe default city/coordinates instead of crashing.
- Ingestion jobs validate coordinates before starting, and invalid values are normalized to a default location.
- Optional AI/vector dependencies such as Qdrant and sentence-transformers degrade gracefully when not installed, returning safe fallback behavior rather than runtime exceptions during app startup.
- The backend test suite is used as the stability gate for these crash-safe defaults.

## Frontend defaults

- The frontend uses the local FastAPI endpoints through the Vite `/api` proxy. Start the backend before loading live data.
- The mobile shell implements onboarding, permission/ingestion handoff, Now, Nearby, Plan, Ask, Profile, place detail, saved places, and `/dev/ui-kit` with loading, error, empty, and offline-friendly states.
- The first frontend open shows a Sign up/Login gate backed by SQLite users, PBKDF2 password hashes, and signed bearer sessions. Profile includes Logout; configure `AUTH_SECRET` before deploying.
- Place names, scores, distances, images, weather, and briefing values shown after loading come from API responses; unavailable values are hidden or described as unavailable.
- `/api/now` and `/api/nearby` now use browser coordinates with bounded Nominatim, Open-Meteo, and Overpass requests. Provider failures return partial/empty data instead of crashing.
- Location ingestion jobs are persisted in SQLite and can be queried after process-local memory is gone.
- The app is installable as a PWA through `frontend/public/manifest.webmanifest` and caches its shell for offline reopening. Native Capacitor packaging, interactive Leaflet maps, and external image enrichment remain optional follow-up integrations.

## Notes

The project is intentionally incremental: live place/context retrieval and the resilient mobile product shell are in place, while provider-specific enrichment and native packaging can be added without changing the core API contract.
