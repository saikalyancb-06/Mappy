# GeoGuide

A location-aware travel intelligence app. GeoGuide routes each question to the right sources (spatial search, place knowledge, live weather, safety data, web search), ranks the evidence, and only then asks the LLM to write a short answer that cites that evidence. The LLM writes the answer; the data provides the facts.

The demo uses **Hampi** (Karnataka), but Hampi is only a *data pack*: no code knows about it, and any destination can be added by dropping in a pack.

```
geoguide/
├── backend/   FastAPI · SQLite or PostgreSQL+PostGIS+pgvector · Groq · SerpApi · Open-Meteo
└── frontend/  React + Vite PWA (Now · Nearby · Plan · Ask · Profile)
```

## Quick start

### 1. Backend

```bash
cd geoguide/backend
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then put your keys in .env (see below)
uvicorn app.main:app --port 8000 --reload
```

Put your keys in `geoguide/backend/.env`. They stay on the server and are never sent to the browser:

```
GROQ_API_KEY=gsk_...
SERPAPI_KEY=...
AUTH_SECRET=<any long random string>
```

On first start the backend creates the database, imports every pack in `backend/data/packs/` (Hampi), and downloads the embedding model in the background (about 120 MB, once). Until the model is ready, knowledge search uses keyword (BM25) matching and says so in its responses. Check `http://localhost:8000/api/health` to see which services are configured.

### 2. Frontend

```bash
cd geoguide/frontend
npm install
npm run dev        # opens on http://localhost:5173, proxies /api to :8000
```

### 3. Demo walkthrough (Hampi)

1. Sign up, then pick interests.
2. On **Start with a place**, choose **Hampi** and optionally turn on your location. The two stay separate: "near me" always means your device location, and "in Hampi" means Hampi.
3. **Now** shows the local time, weather, daylight left, active advisories (with their stored severity), events, and a grounded briefing.
4. **Nearby** lists ranked places with distance, open status, fee, visit length, access and "why this suits you". Toggle between *Hampi* and *Near me*.
5. **Plan** builds a 2h, 4h or full-day plan that checks opening hours, travel time and cost, then re-plans for cheaper, greener or less walking.
6. **Ask**: try these:
   * *What should I visit in Hampi tomorrow?* (discovery + forecast + advisories)
   * *Why is Hampi historically important?* (knowledge retrieval)
   * *Coffee shops near me* (GPS + spatial + live maps search; asks for location if it's off)
   * *Where is SLV Hotel in Gandhi Bazaar?* (entity resolution with branch disambiguation)
   * *Is it raining near me?* (weather only, no RAG)
   * *How should I dress for temples?*, *Which places are step-free?*, *Plan my evening around Hampi*

   Tap a numbered chip to see its source. The 🐞 button shows the full retrieval trace in development.
7. **Profile** sets interests, budget, pace, walking and step-free access, which change ranking and plans. Choosing Kannada or Hindi translates answers and keeps place names.

## How a question is answered

```
question ─► QueryIntent (parser: intent, category, entity, place, radius, time, preferences)
        ─► GeoContext   (device GPS w/ freshness ≠ active destination ≠ place named in question)
        ─► Router       (only the sources this intent needs)
              ├─ spatial candidates   PostGIS ST_DWithin | SQLite bbox + haversine
              ├─ entity resolution    name/alias/acronym, branch, locality, category, distance, source agreement
              ├─ knowledge            BM25 + sentence-transformer vectors (pgvector | numpy), RRF, metadata filters
              ├─ live                 Open-Meteo weather, advisories, events, SerpApi maps/web/events
        ─► normalise → dedupe (per-field source priority) → hard filters → ranking (weights in config)
        ─► evidence [E1..En] with provenance
        ─► Groq (bounded evidence) → validator (places, ratings, distances, prices, times, temps, open-now)
              └─ on failure: one repair pass, then a deterministic answer built from the evidence
        ─► structured response: answer, intent, geo_context, results, sources, confidence, notices, trace
```

For example, *"Coffee shops near me"* never calls the LLM for search and never touches RAG. It runs GPS → spatial → (maps search if coverage is thin) → ranking → a short answer.

## Configuration & data

| What | Where |
|---|---|
| Categories, synonyms, OSM mappings, preferences | `backend/data/config/taxonomy.json` |
| Intent cue phrases, radii, query expansions | `backend/data/config/intents.json` |
| Ranking weights, dedup, source priority, entity-resolution weights | `backend/data/config/ranking.json` |
| Itinerary speeds, fares, CO₂, presets | `backend/data/config/itinerary.json` |
| Destination packs (destination, POIs, knowledge, facts, advisories, events, provenance) | `backend/data/packs/<name>/` |

**Add a destination:** copy `data/packs/hampi` as a template, edit the JSON, then run `python -m app.db.seed --pack data/packs/<name>` (or restart with an empty database). Places without a pack still work: GeoGuide pulls OpenStreetMap POIs on demand and uses live maps search.

**PostgreSQL + PostGIS + pgvector:** set `DATABASE_URL=postgresql+psycopg://user:pass@host/db`. Extensions are enabled automatically when available. `/api/health` reports `postgis`/`pgvector`.

**Re-embed knowledge** after changing `EMBEDDING_MODEL`: run `python -m app.retrieval.indexer --force`. The model name and dimension are stored per chunk, and vectors from different models are never compared.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/ask` | `{question, user_location?, active_destination?, selected_place_id?, language?, debug?}` → grounded answer + structured context |
| `GET /api/now` | Briefing, weather, daylight, advisories, events, suggestions |
| `GET /api/nearby` | Ranked places; `origin=auto\|user\|destination`, `category`, `group`, `open_now`, `radius_km` |
| `GET /api/places/{id}` | Place detail with facts, advisories, provenance |
| `POST /api/plan` | Itinerary; `duration=2h\|4h\|full\|minutes`, `preset=balanced\|cheaper\|greener\|less_walking`, `previous` for change explanations |
| `GET /api/destinations`, `/destinations/resolve`, `/destinations/{id}/pack` | Destination search, resolution, and cached knowledge pack |
| `GET /api/weather`, `/api/location/describe`, `/api/config`, `/api/health` | Supporting endpoints |

A location is sent as `{lat, lon, accuracy_m, timestamp}`. Without a timestamp it is ignored, it is flagged as stale after 10 minutes, and it is rejected after 1 hour. There is no default city: if the request has no location and no destination, the API says what it needs.

## Tests

```bash
cd geoguide/backend && python -m pytest -q
cd geoguide/frontend && npm run lint && npm run build
```

The backend suite (81 tests) runs offline against a fictional destination ("Testville"), so it cannot pass by special-casing the demo data. It covers:

* GPS available, missing, stale, low-accuracy or invalid, and "explicit destination vs GPS"
* A labelled intent set, radius and category hard filters, and ranking precision@3
* Entity exact, alias, acronym, branch, ambiguous and not-found cases
* Deduplication with source priority
* Hybrid retrieval and embedding-model isolation
* Weather success and failure, advisory severity and seasons, and web failures
* Validator catching invented facts, the LLM repair and fallback paths
* Itineraries: opening hours, locked stops, and re-plan direction
* The full API
