# GeoGuide

A city-and-date travel intelligence app. The **city** is where (from GPS or a chosen place), the **date** is when (a date picker that re-runs everything), and the **intent** is what (Explore tabs, Ask). GeoGuide routes each question to the right sources (spatial search, place knowledge, live weather, safety data, web search), ranks the evidence, and only then asks the LLM to write a short answer that cites that evidence. The LLM writes the answer; the data provides the facts.

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

On first start the backend creates the database, imports every pack in `backend/data/packs/` (Hampi), imports the organisers' PS-13 dataset (60 cities, see below), and downloads the embedding model in the background (about 120 MB, once). Until the model is ready, knowledge search uses keyword (BM25) matching and says so in its responses. Check `http://localhost:8000/api/health` to see which services are configured.

### 2. Frontend

```bash
cd geoguide/frontend
npm install
npm run dev        # opens on http://localhost:5173, proxies /api to :8000
```

### 3. Demo walkthrough (Hampi)

1. Sign up, then pick interests.
2. On **Start with a place**, choose **Hampi** and optionally turn on your location. The two stay separate: "near me" always means your device location, and "in Hampi" means Hampi.
3. **Explore** is the first screen: *Explore {city}* for the city resolved from your GPS or your chosen place. Pick a date (arrows, calendar, *Today* / *Tomorrow* / *This weekend*) and everything is recomputed for that city and day: a grounded briefing (with **Listen**), what's happening, the weather for that date (labelled forecast, recorded, dataset record or typical-for-date), the season, local tips (each says what it's based on), history, top attractions and active advisories. Tabs: Overview · What's happening (day / weekend / 7 days, grouped as Festivals, Music, Culture, Arts, Sports, Food…) · Things to do · Food · Hotels · History · Culture · Ask. The organisers' data gives a good demo: **Bengaluru on 24 Sep 2026** has *no verified events* (and says so), **20 Oct 2026** has the Boat Race and Monsoon Music Nights, and a date months ahead shows typical weather rather than a made-up forecast.
4. **Nearby** lists ranked places with distance, open status, cost against your budget, visit length, access, a confidence badge and "Why this place" bars. Toggle between *Hampi* and *Near me*. You can:
   * type what you want in plain words, e.g. *peaceful, no museums, under ₹500, within 20 min by bike*. The chips show exactly what was understood;
   * switch to *Popular*, *Local favourites* or *Hidden gems*, or to *Within my budget*;
   * open the **Hotels** tab for the best stays around you or the destination, sorted by best match, cheapest, nearest or top rated, with live nightly rates when web search is configured;
   * use the search box for any place, hotel or destination you've heard of. It tolerates typos (*vitala temple*).
5. **Plan**: describe the day (*Temples and a sunset spot, no museums, under ₹800*, *Free from 4–8 PM, by bike*), pick a duration and transport, and add must-see places from search. The plan respects opening hours, travel time and the budget. Then re-optimise it from where you are: **Done**, **Staying +15/+30 min**, **Skip**, or **Running 20 min late**. Presets re-plan for cheaper, greener or less walking.
6. **Ask**: try these:
   * *What should I visit in Hampi tomorrow?* (discovery + forecast + advisories)
   * *Why is Hampi historically important?* (knowledge retrieval)
   * *Coffee shops near me* (GPS + spatial + live maps search; asks for location if it's off)
   * *Where is SLV Hotel in Gandhi Bazaar?* (entity resolution with branch disambiguation)
   * *Is it raining near me?* (weather only, no RAG)
   * *How should I dress for temples?*, *Which places are step-free?*, *Plan my evening around Hampi*
   * *Compare Virupaksha Temple and Vittala Temple* (side-by-side table)
   * *Something on my way from Hampi Bazaar to Vittala Temple* (route corridor with detour estimates)
   * *Give me 3 places only, quiet and free* ("don't waste my time": low-confidence places are dropped)

   Tap a numbered chip to see its source. The 🐞 button shows the full retrieval trace in development.
7. **Profile** sets interests, a daily spending limit with currency, usual transport, pace, walking and step-free access. These change ranking, budget fit and plans. Choosing Kannada or Hindi translates answers and keeps place names.

## City + date context engine

```
GPS ─┐                         ┌─ place knowledge (RAG, place domain: history, overview…)
     ├─► city resolver ─► CITY ├─ culture (RAG, culture domain: culture, etiquette, seasonal)
pick ┘   (coverage → geocoder) ├─ events & festivals: city_id = ? AND start ≤ day ≤ end   (never a radius)
                      DATE ───►├─ weather for the date: forecast │ recorded │ dataset record │ typical, labelled
                               ├─ season (country calendar / hemisphere) + peak months
                               ├─ attractions, advisories active on that date
                               └─ tips = rules over the above, each with its basis
                                         ▼
                         grounded briefing (LLM) + validator ─► text / voice / follow-ups
```

* **Events are date-native and city-scoped.** A multi-day event matches every day it spans. Distance from you is only added for display. Two kinds are kept apart: *festivals* (recurring or cultural) and *live events* (concerts, exhibitions, matches, fairs). A festival that is only associated with a city, with no confirmed dates, appears separately as "dates not confirmed" and never as happening.
* **Source order:** curated and organiser data → live event APIs (Ticketmaster, optional, where it has coverage) → web event listings (Google Events via SerpApi). Listings are kept only if their dates can be read and overlap the requested day or range. They are stored with their source and a *verified* timestamp. The LLM is never a source of events.
* **Zero stays zero.** If nothing matches, the response is `events: []` with `event_status: "no_verified_events_found"`. The model receives `VERIFIED EVENTS: None` plus a hard rule not to infer or substitute. The validator rejects any event or festival name that isn't in the evidence, and the answer falls back to a deterministic one built from the evidence.
* **Follow-ups keep the context.** Ask sends the selected date. "What's happening here this weekend?" resolves *here* from GPS and *this weekend* against the selected date.

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

### Organisers' dataset (PS-13)

`backend/data/sources/ps13/PS-13.db` is imported read-only at startup (`AUTO_IMPORT_PS13=true`; the mapping is in `data/config/ps13_mapping.json`). The import is additive: a city that matches an existing destination by name and distance (for example Hampi) is merged into it, and other cities become new destinations. It loads POIs, hotels, knowledge and facts, advisories (with the severity as issued), events and daily weather. Money stays exact decimal text with its ISO currency. Every record is tagged with source `dataset` (confidence 0.6), so curated and live data win field conflicts, and disagreements are shown as "sources disagree" rather than hidden. When Open-Meteo is unreachable, the stored `weather_daily` record is used and labelled *not a live forecast*.

### Constraints, confidence and cost

* **Constraint engine** (`app/query/constraints.py`, vocabulary in `intents.json → constraints`) turns free text into structured filters: time window, time available, max cost and currency, price level, minimum rating, travel mode and max travel minutes, open now, crowd, party, "N places only", popular/local/hidden gems, include/exclude categories, avoided tags, and route endpoints. Hidden gems, local favourites and popular are defined by popularity scores and tags in `ranking.json → ranking_modes`, not by hand-picked lists.
* **Confidence** (`app/ranking/confidence.py`) scores entity, location, hours, price and freshness separately. Conflicts between sources (location more than 1 km apart, open vs closed, different fees) cap the overall confidence.
* **Cost**: every place and hotel shows its entry fee or nightly rate against your daily budget. Nightly rates come only from a live source (SerpApi Google Hotels). The dataset has no rates, so none are invented.

**Add a destination:** copy `data/packs/hampi` as a template, edit the JSON, then run `python -m app.db.seed --pack data/packs/<name>` (or restart with an empty database). Places without a pack still work: GeoGuide pulls OpenStreetMap POIs on demand and uses live maps search.

**PostgreSQL + PostGIS + pgvector:** set `DATABASE_URL=postgresql+psycopg://user:pass@host/db`. Extensions are enabled automatically when available. `/api/health` reports `postgis`/`pgvector`.

**Re-embed knowledge** after changing `EMBEDDING_MODEL`: run `python -m app.retrieval.indexer --force`. The model name and dimension are stored per chunk, and vectors from different models are never compared.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/ask` | `{question, user_location?, active_destination?, selected_place_id?, date?, language?, debug?}` → grounded answer + structured context |
| `GET /api/now` | Briefing, weather, daylight, advisories, events, suggestions |
| `GET /api/context` | City + date context: `destination_id` or GPS, `date=YYYY-MM-DD` (default: the city's today) → city, season, weather (with `basis`), events, tips, about, culture, attractions, advisories, grounded briefing |
| `GET /api/events` | What's happening in the city: `date` plus `when` (*this weekend*, *next week*, *Oct 22*, *in October*) or `end`; grouped by category, with sources checked and `event_status` |
| `GET /api/nearby` | Ranked places; `origin=auto\|user\|destination`, `category`, `group`, `open_now`, `radius_km`, `text` (free-text constraints), `ranking_mode`, `travel_mode`, `within_budget` |
| `GET /api/hotels` | Best stays; `sort=best\|cheapest\|nearest\|top_rated`, `check_in`, `nights`, `max_price`, `min_stars`, `within_budget` |
| `GET /api/search` | `q`, `kind=place\|stay\|destination`: typo-tolerant search over stored places, hotels and destinations, then live maps |
| `GET /api/places/{id}` | Place detail with facts, advisories, provenance, confidence, cost for you |
| `POST /api/plan` | Itinerary; `duration=2h\|4h\|full\|minutes`, `preset=balanced\|cheaper\|greener\|less_walking`, `wishes` (free text), `previous` for change explanations, `replan={previous, completed_ids, skipped_ids, current_stop_id, extra_minutes, now}` |
| `GET /api/destinations`, `/destinations/resolve`, `/destinations/{id}/pack` | Destination search, resolution, and cached knowledge pack |
| `GET /api/weather`, `/api/location/describe`, `/api/config`, `/api/health` | Supporting endpoints |

A location is sent as `{lat, lon, accuracy_m, timestamp}`. Without a timestamp it is ignored, it is flagged as stale after 10 minutes, and it is rejected after 1 hour. There is no default city: if the request has no location and no destination, the API says what it needs.

## Tests

```bash
cd geoguide/backend && python -m pytest -q
cd geoguide/frontend && npm run lint && npm run build
```

The backend suite (129 tests) runs offline against a fictional destination ("Testville"), so it cannot pass by special-casing the demo data. It covers:

* GPS available, missing, stale, low-accuracy or invalid, and "explicit destination vs GPS"
* A labelled intent set, radius and category hard filters, and ranking precision@3
* Entity exact, alias, acronym, branch, ambiguous and not-found cases
* Deduplication with source priority
* Hybrid retrieval and embedding-model isolation
* Weather success and failure, advisory severity and seasons, and web failures
* Validator catching invented facts, the LLM repair and fallback paths
* Itineraries: opening hours, locked stops, and re-plan direction
* Dataset import against a mini database in the organisers' exact schema, the dataset weather fallback, and source conflicts
* The constraint engine, hidden gems, budget fit, hotels with live rates, plan wishes, re-planning from the current state, route detours, compare and search
* City + date: date ranges, city resolution, multi-day overlap, city scoping, cancelled and associated festivals, live listings (dated / undated / duplicate), weather basis per date, the three judge states (today, festival date, empty date), and an LLM that invents a festival being rejected
* The full API

## Known limits

* The PS-13 dataset is synthetic. Its facts are treated as a medium-confidence source and labelled.
* Hotel nightly rates need `SERPAPI_KEY`. Without it, hotels are ranked on class, guest score and distance and marked "price not available".
* Travel times, fares and detours are straight-line estimates (haversine × detour factor) and are labelled as estimates. There is no routing engine.
* Official city and tourism websites are not scraped as a separate source; they reach GeoGuide only through web event listings. Ticketmaster is used only when `TICKETMASTER_API_KEY` is set.
* Past dates use stored records only; live listings cover today onwards. Beyond the 16-day forecast, weather is the dataset's record for that date or the average of the last 3 years, never a forecast.
* Not built: image search, offline packs and environmental or habitat data. No data source for them is connected.
