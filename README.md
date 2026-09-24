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
TICKETMASTER_API_KEY=...        # optional: structured listings in Ticketmaster markets (skipped for India)
AUTH_SECRET=<any long random string>
EVENT_MODERATOR_EMAILS=you@example.com   # optional: accounts that review organiser event submissions
CITY_ENRICHMENT_ENABLED=true             # build a city's guide in the background when it is selected
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
5. **Plan**: tap **Swipe to pick places** for a card deck of candidates for your wishes, taking turns between them (a temple, then a sunset spot, then a park…). Swipe or drag right to go, left to skip; the ✕ / ♥ buttons, the arrow keys and undo do the same. Places you like are kept in the plan, and places you skip are left out. Describe the day (*Temples and a sunset spot, no museums, under ₹800*, *Free from 4–8 PM, by bike*), pick a duration and transport, and add must-see places from search. The plan respects opening hours, travel time and the budget. Then re-optimise it from where you are: **Done**, **Staying +15/+30 min**, **Skip**, or **Running 20 min late**. Presets re-plan for cheaper, greener or less walking.
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
7. **Feedback.** On any place, tap **How was this place?**: pick a rating, then any vibes (Peaceful, Scenic, Lively… or *Other*), then optionally what you liked or didn't, and a line of text. Plans also ask after you mark a stop *Done*. Places show **What visitors say** (labelled as opinions, and as sample data where synthetic). Your **Profile** shows the vibes you enjoy and what you avoid, and recommendations and events shift towards them.
8. **Profile** sets interests, a daily spending limit with currency, usual transport, pace, walking and step-free access. These change ranking, budget fit and plans. Choosing Kannada or Hindi translates answers and keeps place names.

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

## City intelligence

Selecting a city is the moment GeoGuide builds its knowledge of that city. See **[docs/city-intelligence.md](docs/city-intelligence.md)** for the architecture audit, the design and API examples.

* **Registry.** Every chosen city gets one canonical record in `destinations`: deterministic id, aliases, enrichment status (`NOT_STARTED` … `READY`, `PARTIAL`, `STALE`) and per-component freshness.
* **Enrichment in the background.** Category searches (attractions, landmarks, museums, parks, viewpoints and so on) run through SerpApi's Google Maps results behind a `PlaceSearchProvider` interface. Results are normalised, checked against the city's boundary, de-duplicated and upserted idempotently. City knowledge (overview, history, culture, geography and so on) comes from Wikipedia with attribution. Provider reviews add vibe evidence at a lower weight than GeoGuide feedback.
* **Database first.** Once prepared, questions about places and the city are answered from the database. Live Maps search runs only when the city isn't ready, coverage is thin or a place is unknown, and reusable finds are stored. Events, weather and "open now" stay live.
* **Neutral recommendation policy.** One deterministic gate keeps permanently closed places out of suggestions. Deployments can list categories to exclude, and travellers can leave places of worship of every faith out of suggestions.

## Speed

Independent provider calls run in parallel, connections are pooled, responses are gzipped, the Explore page doesn't wait for the AI briefing, and the app caches recent reads. The cold Explore load dropped from 6.5 s to 2.5 s with realistic provider latency, and the question-parsing CPU is about 5× lower. See **[docs/performance.md](docs/performance.md)**.

## Trip tools

* **Plan export:** "Add to calendar" downloads the day as an `.ics` file in the destination's timezone. "Route in Maps" opens every stop as a Google Maps route, and "Share" uses the phone's share sheet.
* **More like this:** each place shows similar places nearby (same category or kind, shared vibes, rating, distance). The recommendation policy applies.
* **Saved places:** Profile lists what you saved; these are also the plan's must-sees.
* **Offline:** screens you've opened keep working without a connection, and "Save for offline" stores a whole city (places with opening hours, guide, events, safety notes) on the device. Nearby, search and Ask then answer from it. See **[docs/offline.md](docs/offline.md)**.

## Feedback, vibes and events

See **[docs/feedback-and-events.md](docs/feedback-and-events.md)** for the data model, formulas, providers and example API requests and responses. In short:

* **Feedback → signals.** Each rating, set of vibe chips, pair of liked/disliked chips and text becomes normalised rows. Place vibe profiles are shrunk towards the place's own tags until enough feedback exists. Traveller preferences are recency-weighted, with a cold start. Two ranking components are added: `vibe` (user-specific, including aversions like "too crowded") and `community` (the place-level rating). Both are neutral without data.
* **Events.** Stored records (including reviewed organiser submissions), official festival calendars, Ticketmaster (where it has coverage) and Google Events are queried first, with a validated web search as the long-tail fallback. Results are normalised, checked against the dates and the destination's boundary, linked to known venues, de-duplicated, scored for confidence and freshness (expired events are removed), and ranked by time, distance, relevance, confidence and vibe. Nothing comes from the RAG store or the LLM, and an empty result stays empty.
* **Synthetic bootstrap feedback.** 800 records, clearly labelled, are loaded for development. Set `AUTO_SEED_FEEDBACK=false` and `FEEDBACK_INCLUDE_SYNTHETIC=false` to exclude them.

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
| City extent, city aliases, "city centre" phrases, geocoder bias | `backend/data/config/geo.json` |
| Official festival/holiday calendars | `backend/data/sources/festival_calendars/*.json` |
| Destination packs (destination, POIs, knowledge, facts, advisories, events, provenance) | `backend/data/packs/<name>/` |

### Organisers' dataset (PS-13)

`backend/data/sources/ps13/PS-13.db` is imported read-only at startup (`AUTO_IMPORT_PS13=true`; the mapping is in `data/config/ps13_mapping.json`). The import is additive: a city that matches an existing destination by name and distance (for example Hampi) is merged into it, and other cities become new destinations. It loads POIs, hotels, knowledge and facts, advisories (with the severity as issued), events and daily weather. Money stays exact decimal text with its ISO currency. Every record is tagged with source `dataset` (confidence 0.6), so curated and live data win field conflicts, and disagreements are shown as "sources disagree" rather than hidden. When Open-Meteo is unreachable, the stored `weather_daily` record is used and labelled *not a live forecast*.

### Constraints, confidence and cost

* **Constraint engine** (`app/query/constraints.py`, vocabulary in `intents.json → constraints`) turns free text into structured filters: time window, time available, max cost and currency, price level, minimum rating, travel mode and max travel minutes, open now, crowd, party, "N places only", popular/local/hidden gems, include/exclude categories, avoided tags, and route endpoints. Hidden gems, local favourites and popular are defined by popularity scores and tags in `ranking.json → ranking_modes`, not by hand-picked lists.
* **Confidence** (`app/ranking/confidence.py`) scores entity, location, hours, price and freshness separately. Conflicts between sources (location more than 1 km apart, open vs closed, different fees) cap the overall confidence.
* **Wishes are items with quantities.** *"temples and the sunset spot, no museums, 500 and a park"* means: temples (as many as fit), **1** sunset spot, **1** park, no museums, and a budget of 500 in the local currency. A negation stops at the next item (*"no museums and a park"* still wants a park). The plan covers every item before adding more of one kind, caps singular items, picks within the budget together (entry fees plus estimated transport), and schedules a sunset spot about 45 minutes before the forecast sunset. A sunset spot is a viewpoint, or a scenic place tagged *sunset*, never a sunset-tagged bar or market. Each item comes back with a status (planned, none stored here, over budget with the price, or didn't fit with the reason), so an empty or partial plan explains itself. Nearby takes turns between the items the same way.
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
| `GET /api/events` | Verified events for the destination (or `near=me`): `date` plus `when` (*tonight*, *this weekend*, *next week*, *Oct 22*, *in October*, *during my trip*) or `end`; `q`, `category`, `free`, `festival`, `radius_km`; with sources checked, counts and `event_status` |
| `GET /api/events/overview` | The next 30 days of verified events split into the tabs that have events (Today, Tonight, This weekend, Festivals, Music, Free…) |
| `POST /api/feedback`, `GET /api/feedback/vocabulary` | Post-visit feedback (rating, vibes, liked/disliked, text) and its vocabulary |
| `GET /api/places/{id}/community`, `GET /api/me/vibes` | What visitors say about a place; your learned vibe profile (labels only) |
| `GET /api/feedback/analytics` | Development only: vibe/aspect distributions, volume, confidence, ranking changes caused by feedback |
| `GET /api/nearby` | Ranked places; `origin=auto\|user\|destination`, `category`, `group`, `open_now`, `radius_km`, `text` (free-text constraints), `ranking_mode`, `travel_mode`, `within_budget` |
| `GET /api/hotels` | Best stays; `sort=best\|cheapest\|nearest\|top_rated`, `check_in`, `nights`, `max_price`, `min_stars`, `within_budget` |
| `GET /api/search` | `q`, `kind=place\|stay\|destination`: typo-tolerant search over stored places, hotels and destinations, then live maps |
| `GET /api/places/{id}` | Place detail with facts, advisories, provenance, confidence, cost for you |
| `POST /api/plan/deck` | Swipe cards for the wishes (same body as `/api/plan`): `cards` taking turns between wishes, each labelled with its wish, plus `wish_coverage` (nothing stored / over budget). Liked ids go back as `locked_ids`, skipped ones as `excluded_ids` |
| `POST /api/plan` | Itinerary; `duration=2h\|4h\|full\|minutes`, `preset=balanced\|cheaper\|greener\|less_walking`, `wishes` (free text), `previous` for change explanations, `replan={previous, completed_ids, skipped_ids, current_stop_id, extra_minutes, now}` |
| `GET /api/destinations`, `/destinations/resolve`, `/destinations/{id}/pack` | Destination search, resolution, and cached knowledge pack |
| `GET /api/weather`, `/api/location/describe`, `/api/config`, `/api/health` | Supporting endpoints |

A location is sent as `{lat, lon, accuracy_m, timestamp}`. Without a timestamp it is ignored, it is flagged as stale after 10 minutes, and it is rejected after 1 hour. There is no default city: if the request has no location and no destination, the API says what it needs.

## Tests

```bash
cd geoguide/backend && python -m pytest -q
cd geoguide/frontend && npm run lint && npm run build
```

The backend suite (190 tests) runs offline against a fictional destination ("Testville"), so it cannot pass by special-casing the demo data. It covers:

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
* Plan wishes: negation scope, amounts without a currency, quantities, one-of caps, sunset timing, explained gaps, liked places not forced into swipe order, and the swipe deck
* Feedback and vibes: peaceful vs lively travellers, crowd- and cost-averse travellers, cold start, a single review, conflicting feedback, new places, new vibes, a user's dislike vs a place's global quality, and the synthetic bootstrap
* Events: tonight, weekend, destination vs physical location, near-me distance, venue linking, multi-provider de-duplication, stale and expired listings, confidence by source, free/festival/category filters, provider failure, web fallback, vibe-personalised ranking, and Ticketmaster mapping
* The full API

## Known limits

* The PS-13 dataset is synthetic. Its facts are treated as a medium-confidence source and labelled.
* Hotel nightly rates need `SERPAPI_KEY`. Without it, hotels are ranked on class, guest score and distance and marked "price not available".
* Travel times, fares and detours are straight-line estimates (haversine × detour factor) and are labelled as estimates. There is no routing engine.
* Official city and tourism websites are not scraped as a separate source; they reach GeoGuide only through web event listings. Ticketmaster is used only when `TICKETMASTER_API_KEY` is set, and only in the countries listed in `events.json → provider_coverage`. It is skipped for Indian cities, which rely on Google Events and on site-restricted searches of BookMyShow, District, Insider, Skillboxes, Townscript and AllEvents.
* Past dates use stored records only; live listings cover today onwards. Beyond the 16-day forecast, weather is the dataset's record for that date or the average of the last 3 years, never a forecast.
* Not built: image search, offline packs and environmental or habitat data. No data source for them is connected.
