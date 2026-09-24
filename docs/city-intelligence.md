# City intelligence: architecture audit and migration plan

This document records what GeoGuide already had before the "city intelligence" change, what was reused, what had to change, and how the new pieces fit together. The implementation follows the plan in section 10. Sections 11 onward describe the result.

---

## Part A: Audit (before the change)

### 1. Existing architecture

| Layer | What exists |
|---|---|
| Frontend | React 19 + Vite SPA (`geoguide/frontend`). `App.jsx` holds two separate pieces of geographic state: the **destination** (persisted in `localStorage`) and the **device location** (`useDeviceLocation`, session only). Views: Explore (city + date context), Nearby, Plan (swipe deck), Ask, Profile. Everything goes through `src/api.js`, which calls the GeoGuide backend only. |
| Backend | FastAPI (`app/main.py`). Routes live in `app/api/routes/*`: `destinations`, `places`, `ask`, `plan`, `context` (city context and events), `feedback`, `submissions`, `auth`, `system`. |
| Database | SQLAlchemy models (`app/db/models.py`). **SQLite** is the zero-setup default. **PostgreSQL + PostGIS + pgvector** is the production target: `app/db/session.py` detects the extensions and adds a `geom` column, a GiST index and a trigger to `pois`, plus a pgvector column to `knowledge_chunks`. There are no migration files. Upgrades add missing columns automatically. Bumping `SCHEMA_VERSION` **drops every non-user table** (they are re-imported from packs), so it must not be bumped casually. |
| Retrieval | `app/geo/spatial.py` generates spatial candidates: PostGIS `ST_DWithin` on Postgres, and a bounding box plus haversine on SQLite. `app/retrieval/knowledge.py` is hybrid knowledge RAG (metadata filter, BM25, vector search, RRF fusion) over `knowledge_chunks`. Structured questions already avoid RAG. |
| Search | `app/search/serpapi.py` is a server-side client for the `google`, `google_maps`, `google_news`, `google_events` and `google_hotels` engines, with a shared cache. `app/search/normalizer.py` maps Maps results to `Candidate`, and `app/search/aggregator.py` handles de-duplication (provider id, name plus distance, contact details). |
| Discovery | `app/services/discovery.py` is the single candidate pipeline for Nearby, Ask, Plan and Now. It checks the DB first, pulls **OpenStreetMap live** (persisted, but with `destination_id=NULL`) when an area has no data, and uses **SerpApi Maps** when coverage is thin. **These results were transient**: they were never stored, so every similar question paid for another search. |
| Recommendation | `app/ranking/ranker.py` ranks candidates. `_hard_filter` is the one gate every recommendation path passes through: discovery, nearby, plan, hotels, and look-ups via `rank()`. |
| AI answers | `app/services/query_service.py` classifies intent (rules in `app/query/parser.py`, optional LLM refinement), builds evidence and generates a grounded answer (`app/llm/*`). The validator rejects names that are not in the evidence. |
| Cities | `destinations` holds 60 dataset cities (PS-13) plus curated packs. `app/geo/city.py` and `app/geo/geocoding.py` handle resolution, with aliases in `data/config/geo.json`, a population-based city extent and biased Nominatim. **Non-stored places** (for example a town found through Nominatim) were never registered. They lived only in the browser's `localStorage`, plus an OSM "prefetch" job whose POIs were not linked to any city. |
| Events | `app/events/*` is a separate, live, multi-provider domain: stored records, official calendars, Ticketmaster (by country), Google Events, validated web search and reviewed submissions. |
| Feedback and vibes | `feedback`, `feedback_vibes`, `place_vibe_profiles` and `user_vibe_preferences` tables (`app/feedback/*`). Place vibe profiles are Bayesian aggregates of GeoGuide feedback, with confidence. **Provider reviews were not used.** |
| Caching | `cache_entries`: a namespaced TTL cache in the DB plus memory. It covers geocoding, SerpApi, weather, Overpass and a 10-minute destination pack cache (`app/knowledge/destination_pack.py`). |
| Background jobs | `ingestion_jobs` plus Python threads, used by `/api/destinations/prefetch`. There is no queue service. |
| Observability | `app/core/logging.Trace` writes a per-request JSON trace, plus structured log lines. |

### 2. Relevant files

`app/db/models.py`, `app/db/session.py`, `app/geo/{geocoding,city,geo_context,spatial}.py`, `app/search/{serpapi,normalizer,aggregator}.py`, `app/services/{discovery,query_service,search}.py`, `app/ingestion/overpass.py`, `app/retrieval/knowledge.py`, `app/ranking/ranker.py`, `app/feedback/{nlp,aggregate,signals}.py`, `app/api/routes/destinations.py`, `frontend/src/{App.jsx,api.js,views/StartView.jsx,views/ExploreView.jsx}`.

### 3. Existing schema (relevant tables)

- **`destinations`**: id, name, region, country, country_code, lat, lon, coverage_radius_km, timezone, currency, languages, summary, population, peak_months, season_profile, source, data_source_id, curated.
- **`entity_aliases`**: type, id, alias, normalized.
- **`pois`**: id, destination_id, name, normalized_name, kind, category, tags, lat, lon, address, neighborhood, description, opening_hours, rating, review_count, price_level, phone, website, source, source_url, source_id, data_source_id, confidence, fetched_at, updated_at, plus cost, access and stay fields.
- **`knowledge_chunks`**: document_id, destination_id, poi_id, kind, category, title, content, source, source_url, confidence, embedding.
- **`cache_entries`**, **`ingestion_jobs`**, and the events and feedback tables.

### 4. Existing retrieval pipeline

Question → rule parser (intent, category, place, constraints) → `GeoContext` (destination / query place / GPS) → handler:
- discovery and nearby use spatial candidates, then OSM/SerpApi when thin, then rank;
- knowledge uses hybrid RAG, then a web search when the RAG results are weak;
- events use the event engine.

The results then go to evidence, a grounded LLM answer and the validator.

### 5. Existing search pipeline

SerpApi `google_maps` was used **per request** from `discover()` whenever there were fewer than `min_results_before_web` stored places. Results were ranked and returned but **not persisted**. OSM results were persisted without a city.

### 6. Existing city and location flow

The start screen lists stored destinations (live, closest first) or resolves any place through Nominatim. Choosing a non-stored place triggers `POST /api/destinations/prefetch` (OSM + weather into unlinked rows). There is no city record, no completeness state and no refresh policy.

### 7. Existing recommendation flow

`discover()` → `aggregate()` → `rank()` → `_hard_filter` → scoring (distance, rating, preferences, vibe, community, weather and so on) → top N.

### 8. What can be reused

- `destinations` as the **City Registry** (extended, not duplicated).
- `pois` as the POI store: it already has most of the fields in the specification.
- `knowledge_chunks` for city knowledge documents: they already carry kind, category, source and embedding.
- `ingestion_jobs` and background threads for enrichment progress.
- The SerpApi client's HTTP, cache and error handling.
- `aggregator.same_entity` for de-duplication.
- `city_extent_km` and aliases for boundaries and canonical identity.
- The ranker's `_hard_filter` as the single recommendation gate.
- The feedback NLP (`app/feedback/nlp.py`) to extract vibe evidence from review text.

### 9. What must change

1. Every chosen city, including non-stored ones, needs a **deterministic registry entry** with enrichment status, data version and timestamps.
2. Enrichment must track **per-component state and freshness** (overview, attractions, museums, parks and so on) so that partial enrichment and refreshes work.
3. Google Maps results must be **normalised and persisted** through a provider abstraction, with boundary checks, de-duplication and idempotent upserts.
4. Discovery must become **database-first** once a city is prepared, with an explicit and observable routing decision for live look-ups.
5. There must be **one recommendation policy function** called from the ranking gate, from knowledge retrieval and from the city place endpoints.
6. Provider reviews must feed the **place vibe profile as separate evidence**, never overwriting GeoGuide feedback or objective facts.
7. The frontend should prepare a city when it is chosen and show progress. It never talks to providers.

### 10. Migration plan (as implemented)

| Step | Change | Compatibility |
|---|---|---|
| 1 | Add columns to `destinations` (enrichment_status, normalized_name, data_version, last_enriched_at, last_refreshed_at, external_ids, boundary_radius_km, created_at, updated_at) and to `pois` (external_place_id, subcategory, images, metadata_json, last_verified_at, expires_at, provider_raw_id). Add the tables `city_enrichment_components`, `provider_raw_records`, `place_review_signals`. Add indexes with `CREATE INDEX IF NOT EXISTS`. | Additive only. **No `SCHEMA_VERSION` bump**, so no data is dropped. Works on SQLite and Postgres. |
| 2 | `app/cities/registry.py`: `get_or_create_city()` with deterministic ids (`city-<hash(normalized name, country, rounded coords)>`) and alias-aware lookup. | Existing ids are unchanged. |
| 3 | `app/places/providers`: `PlaceSearchProvider` plus `SerpApiPlaceProvider` (`engine=google_maps`, `type=search`, and `type=place` for details). `app/places/normalize.py`, `boundary.py`, `store.py` (upsert by `(destination_id, source, external_place_id)`). | The frontend never sees provider data. |
| 4 | `app/cities/enrichment.py`: configurable category queries (`data/config/city_intelligence.json`), bounded concurrency, idempotent partial enrichment, per-class freshness, observability. | It replaces nothing. The OSM prefetch stays available as a secondary source. |
| 5 | `app/cities/router.py`: routes each question to the DB, live Maps, web/events, weather or a hybrid. `discover()` asks it whether live search is allowed, and live results with reusable place data are persisted. | Behaviour for unprepared cities is unchanged: live search is still used. |
| 6 | `app/policy/recommendation.py`: `is_recommendation_excluded()` is called from `_hard_filter`, from knowledge retrieval and from the city place listings. | It is religion-neutral; see section 16. |
| 7 | Review-derived vibe evidence (`place_review_signals`) is blended into place vibe profiles with a lower weight than GeoGuide feedback. | Neutral when no reviews exist. |
| 8 | API: `POST /api/destinations/ensure`, and `GET /api/destinations/{id}/status`, `/knowledge`, `/places`, plus `POST /api/destinations/{id}/enrich`. Place details and place search reuse the existing `GET /api/places/{id}` and `GET /api/search`, which are now database-first and persist live finds. The frontend prepares the city on selection and polls its status. | The existing `/api/destinations/prefetch` still works. |

---

## Part B: The implemented architecture

### 11. City registry and enrichment states

`destinations` is the registry. `enrichment_status` takes one of: `NOT_STARTED`, `QUEUED`, `ENRICHING`, `PARTIAL`, `READY`, `FAILED`, `STALE`. Identity is deterministic and alias-aware ("Bangalore, India" resolves to Bengaluru), so selecting a city twice never creates two cities. Selecting a city returns immediately. If the city is missing, incomplete or stale, enrichment is queued in the background and the app shows "Preparing your city guide…" with live progress.

### 12. Components and freshness

Each component (`overview`, `attractions`, `landmarks`, `history`, `museums`, `parks`, `viewpoints`, `activities`, `nature`, `family`, `food`, …) has its own row with a status, counts, `last_run_at`, `expires_at` and the last error. A city is `READY` when all required components are fresh, `PARTIAL` when some are, and `STALE` when a required component has expired. Freshness classes live in `data/config/city_intelligence.json`:
- **stable** (overview, history, landmarks): refreshed rarely;
- **semi-stable** (POI lists, ratings, hours): refreshed periodically;
- **dynamic** (events, weather, open-now): never cached in the city dataset and fetched at request time.

### 13. Place discovery and persistence

For each component, the enrichment pipeline runs configurable queries (`"museums in {city}"`, …) through `SerpApiPlaceProvider` with `ll=@lat,lon,zoom`, using bounded concurrency and stopping on quota or authentication errors. Results are then:
1. **normalised** into GeoGuide's schema (category through the taxonomy, rating, review count, price level, hours, website, phone, image, Google place id);
2. **boundary-checked**: within the city's detection extent, or an explicit `boundary_radius_km`, and the address must not name a different city in another country;
3. **de-duplicated**: place id, then external ids, then normalised name plus distance, then address similarity;
4. **upserted** with the deterministic id `gm-<place id>`.

The raw provider payload goes to `provider_raw_records` for auditing and is never read by the application.

### 14. Database-first answering and routing

`app/cities/router.py` decides the route for every discovery or look-up question:
- `database`: the city is prepared and has enough matching places;
- `live_places`: the city is not prepared, stored coverage is thin, or the place is unknown;
- `web` and `events`: current information;
- `weather`;
- `hybrid`: stored POIs plus current state, for example "peaceful places near me that are open now".

The decision and its reason are recorded in the request trace (`route` step) and logged (`database_answer`, `live_search_triggered`, `hybrid_answer`). Live Maps results that carry a place id and coordinates inside the city are persisted, so the next question is answered from the database.

### 15. Knowledge (RAG) scope

City-level knowledge (overview, history, culture, geography) is stored as `knowledge_chunks` with `kind="city_kb"`. It is sourced from Wikipedia's page summary API with attribution, never generated by the LLM. POIs are **not** embedded. They are queried with SQL and spatial filters. Events, weather and reviews are not put in the vector store.

### 16. Recommendation policy

`app/policy/recommendation.py::is_recommendation_excluded(poi, context)` is the single deterministic gate. It is called from the ranker's hard filter, which every suggestion passes through (discovery, nearby, plan and itineraries, hotels, search results and the AI answer's candidate list), and from the city place listings. Semantic (RAG) scores only feed that ranking, so the same gate covers them. It excludes:
- permanently closed places;
- place categories or provider types that the deployment lists in `data/config/recommendation_policy.json` (empty by default);
- places of worship of **every** faith, when the traveller turns on "Leave places of worship out of suggestions" (`exclude_places_of_worship`). Places of worship are detected from category and provider types across all religions: temples, churches, mosques, gurdwaras, synagogues, monasteries and others.

Explicit factual questions ("tell me about <place>", a look-up by name) are not filtered. The policy applies to recommendations and discovery only.

A known limitation: the existing taxonomy files every place of worship under the category id `temple`. The policy therefore also reads the provider's own type labels (Church, Mosque, Gurdwara and so on), so it treats every faith alike.

**Not implemented:** the specification asked for a rule that permanently excludes mosques and masjids, and only those, from all recommendations. That rule singles out one religion while keeping other faiths' places of worship, so it was not built. The neutral per-traveller setting above lets anyone who prefers no religious sites in their suggestions turn them all off.

### 17. Vibes from reviews

When place details are fetched, review snippets run through the same NLP that reads GeoGuide feedback. Matched vibes and aspects are stored in `place_review_signals` with an evidence count, and blended into the place vibe score at a lower weight than GeoGuide feedback (`review_weight` in `vibes.json`). Categories alone never create a vibe. One user's feedback cannot overwrite place facts, because it only adds evidence to the aggregates.

### 18. Privacy

The registry stores destinations, never travellers. Device coordinates are used only for the request at hand. The reverse-geocode cache key is rounded to about 1 km.

### 19. Observability

The log events are: `city_enrichment_started`, `city_enrichment_completed`, `city_enrichment_failed`, `serpapi_request`, `serpapi_failure`, `poi_created`, `poi_updated`, `poi_deduplicated`, `cache_hit`, `cache_miss`, `live_search_triggered`, `database_answer` and `hybrid_answer`. Every enrichment run records its duration, request count, POIs discovered, created, updated, de-duplicated and rejected as out-of-area, and failed components. These figures are exposed on `/api/destinations/{id}/status`.

### 20. API examples

```
POST /api/destinations/ensure            {"name": "Mockhaven", "lat": -23.51, "lon": -46.61, "country": "Testland"}
→ {"city": {"id": "city-d28517d11fab", "name": "Mockhaven", "enrichment_status": "ENRICHING", …}, "created": true,
   "enrichment": "started", "job_id": "…", "status": {"status": "ENRICHING", "progress": 0, "components": {…}}}

GET /api/destinations/city-d28517d11fab/status
→ {"status": {"status": "READY", "progress": 100, "place_count": 78, "components": {"knowledge": {"status": "done", "class": "stable", …}, …}},
   "last_run": {"requests": 28, "discovered": 82, "created": 78, "updated": 10, "rejected": {"outside_boundary": 2}, "details_fetched": 8, "duration_ms": 8016}}

GET /api/destinations/{id}/places?category=museum     → stored places, recommendation policy applied, "excluded_by_policy": n
GET /api/destinations/{id}/knowledge?type=history     → attributed city knowledge documents
POST /api/destinations/{id}/enrich  {"components": ["museums"]}   → refresh specific parts ({"force": true} needs a moderator)
```

The figures above are from a local run against the sandbox's mock providers, not live Google data.

### 21. Configuration

| What | Where |
|---|---|
| Components, query templates, freshness classes, provider limits, routing thresholds, knowledge section mapping | `backend/data/config/city_intelligence.json` |
| Recommendation policy (neutral) | `backend/data/config/recommendation_policy.json` |
| Review evidence weight | `backend/data/config/vibes.json → aggregation.review_weight / review_cap` |
| Environment | `SERPAPI_KEY` (server-side only), `CITY_ENRICHMENT_ENABLED` (default `true`), `WIKIPEDIA_API_URL`, `NOMINATIM_URL` |
