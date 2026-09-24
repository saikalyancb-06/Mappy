# Feedback, vibe intelligence and event discovery

This document covers two additions to GeoGuide. Both plug into the existing pipeline and don't replace any part of it:

1. **Place feedback → vibe intelligence.** Post-visit feedback becomes structured community signals and per-traveller preferences, and these feed the existing ranker as two extra components.
2. **Event discovery.** Events come from structured providers, web discovery, normalisation, validation, de-duplication, confidence scoring and ranking. They are never read from the RAG store or produced by the LLM.

---

## 1. Feedback and vibe intelligence

### Flow

```
POST /api/feedback ─► Feedback service (app/feedback/service.py)
                        ├─ validate (rating 1–5, controlled vibes/aspects, scales, custom vibe rules)
                        ├─ store Feedback + FeedbackVibe/FeedbackAspect links
                        │     selected by the traveller → weight 1.0 (authoritative)
                        │     derived from their text  → weight 0.5 (never overrides selections)
                        └─ recompute ─► Place vibe aggregator (app/feedback/aggregate.py)
                                    ─► User preference engine (same module)
Ranking (app/ranking/ranker.py) ◄─ signals (app/feedback/signals.py): "vibe" + "community" components
```

### Vocabulary (`backend/data/config/vibes.json`)

* **15 vibes:** Peaceful, Aesthetic, Lively, Calm, Romantic, Family-Friendly, Social, Youthful, Cozy, Cultural, Scenic, Adventurous, Premium, Shopping, Nightlife. Each has an emoji, keywords for text analysis, and the place tags/categories that suggest it.
* **Aspects:** 11 positive (good views, less crowded…) and 10 negative (too crowded, too expensive, too noisy…). They live in the `aspects` table, so the vocabulary can grow.
* **Other:** a custom vibe is cleaned and validated (length, letters only, no digits, blocklist). A synonym maps to an existing vibe ("chill" → Calm). A new vibe is stored as *pending* and only takes part in ranking after `promote_after_uses` (5) travellers have used it. No schema change is ever needed.

### Data model (normalised; `app/db/models.py`)

| Table | Purpose |
|---|---|
| `vibes`, `aspects` | Vocabulary rows (controlled + custom), with status and use counts |
| `feedback` | One visit: user, place, city, category, visit date, rating, recommendation, crowd/price level, 5 optional scores, the original text (never overwritten), derived sentiment and analysis, synthetic flag |
| `feedback_vibes`, `feedback_aspects` | Links with origin (`selected` / `custom` / `derived`) and weight |
| `place_vibe_profiles` | Per place × vibe: score, weighted selections, support, confidence |
| `place_aspect_signals` | Per place × aspect: share of (weighted) visitors reporting it |
| `place_feedback_summaries` | Count, average and Bayesian rating, recommend share, confidence, synthetic share |
| `user_vibe_preferences` | Per user × vibe affinity (0.5 = neutral) |
| `user_aspect_preferences` | Per user × characteristic aversion (0..1), user-specific only |

The feedback tables are protected from the schema-upgrade drop (`_USER_TABLES` in `app/db/session.py`).

### Place vibe profile

```
score_v    = (Σ w_f · s_fv + k · prior_v) / (Σ w_f + k)
w_f        = 0.5^(age_days / 365) × rating_weight[rating]    (5★ 1.0 … 1★ 0.6)
s_fv       = 1 if selected, 0.5 if only the text suggested it
prior_v    = 0.5 if a place tag suggests v, 0.35 if its category does, else 0.08
k          = 5  →  confidence = Σ w_f / (Σ w_f + k)
```

This accounts for sample size, recency, rating and explicit selections, and one review can't redefine a place. Negative characteristics (too crowded, too expensive…) are shares shrunk towards a prior taken from the place's own data (a `crowded` tag, a price level ≥ 3). The place-level quality signal is the Bayesian rating `(Σ w·r + 5·3.6) / (Σ w + 5)`.

### User vibe preferences

```
affinity_v = 0.5 + 0.5 · Σ w_f · valence_f · s_fv / (Σ w_f + 2)      valence = (rating − 3) / 2
w_f        = 0.5^(age_days / 120)       (recent behaviour counts more; latest opinion per place)
aversion_a = Σ w_f · [a reported] / (Σ w_f + 2)   (+0.5 when a positive like "less crowded" implies it)
```

* **Status:** `cold_start` (no feedback), `emerging` (1–2 places), `established` (3+).
* **Cold start:** the stated interests give a faint prior, e.g. Nature → Scenic/Peaceful, which real feedback quickly outweighs. With no data, the signal is exactly neutral.

### Ranking integration

The existing ranker gains two components (weights in `ranking.json`; e.g. discovery `vibe 0.10`, `community 0.05`). Both are **0.5 when there is no evidence**, so places and travellers without feedback keep their previous order.

* `vibe` (user-specific). The traveller's centred affinities are matched against how strongly the place shows each vibe, measured around a neutral association of 0.3, so "clearly not peaceful" counts against a lover of peaceful places. The match is scaled by the traveller's confidence. Aversions are then subtracted, weighted by how often visitors report that characteristic (capped at 0.4).
* `community` (place-level). `0.5 + (bayes_rating_normalised − 0.5) · confidence`.

**A user-specific dislike is not a global penalty.** One traveller's 1★ "too noisy" changes their own profile and barely moves the place's Bayesian rating. Other travellers' rankings are unaffected (see `test_user_dislike_is_not_a_global_penalty`).

The ranker also adds reasons such as "Matches vibes you've enjoyed: peaceful, scenic", "Visitors often report \"too crowded\", which you've flagged before" and "Visitors rate it 3.9/5 and call it scenic · peaceful (4 reviews)". Community signals are labelled as opinions and kept apart from place facts.

### Synthetic bootstrap data (development/testing only)

`backend/data/sources/feedback_bootstrap/feedback.jsonl` holds **800 synthetic records**: 164 real stored places, 60 synthetic users, ratings 1–5 in realistic proportions, 83% multi-vibe, 60% with text, and explicit negatives and polarising places. It is generated deterministically by `python -m app.feedback.generate_bootstrap --count 800 --seed 13`. Every record is `is_synthetic: true` and the UI and API say so. Load or reload it with `python -m app.feedback.bootstrap [--reload]`. To exclude it, set `AUTO_SEED_FEEDBACK=false` and `FEEDBACK_INCLUDE_SYNTHETIC=false`.

### Feedback API

| Endpoint | |
|---|---|
| `GET /api/feedback/vocabulary` | Vibes, liked/disliked aspects, rating labels, crowd/price scales |
| `POST /api/feedback` | Submit (anonymous allowed; signed-in feedback also updates your profile) |
| `GET /api/places/{id}/community` | Community signals + recent quotes (synthetic ones flagged) |
| `GET /api/me/vibes` | Your profile as labels, never raw scores |
| `GET /api/me/feedback` | Your feedback history |
| `GET /api/feedback/analytics` | Development only (404 in production): top vibes/dislikes, volume, place and user distributions, custom vibes, and `recommendation_changes` (a ranking with and without feedback, with position moves) |

Request:

```http
POST /api/feedback
Authorization: Bearer <token>

{"place_id": "poi-hampi-hemakuta-hill", "overall_rating": 5, "vibes": ["peaceful", "scenic"],
 "custom_vibes": ["spiritual"], "liked_aspects": ["good_views"], "disliked_aspects": ["too_crowded"],
 "crowd_level": "high", "text_feedback": "Stunning sunset views, but it got crowded near the top."}
```

Response (captured from a local run, trimmed):

```json
{
  "feedback": {
    "feedback_id": "fb_f0d650d67bf24330", "place_name": "Hemakuta Hill", "city": "Hampi", "category": "viewpoint",
    "overall_rating": 5, "recommendation": "loved_it",
    "vibes": ["peaceful", "scenic", "spiritual"], "derived_vibes": ["aesthetic"],
    "liked_aspects": ["good_views"], "disliked_aspects": ["too_crowded"],
    "text_feedback": "Stunning sunset views, but it got crowded near the top.",
    "sentiment": "mixed", "derived_vibe_scores": {"peaceful": 1.0, "scenic": 1.0, "spiritual": 1.0, "aesthetic": 0.5},
    "is_synthetic": false, "source": "user"
  },
  "community": {
    "kind": "community_signal",
    "note": "What visitors report — opinions, not verified facts. Includes synthetic sample feedback used for development.",
    "feedback_count": 6, "confidence": 0.473, "rating": 4.142, "synthetic_share": 0.667,
    "vibe_profile": {"scenic": 0.667, "peaceful": 0.547, "romantic": 0.488, "aesthetic": 0.448, "nightlife": 0.042},
    "top_vibes": [{"key": "scenic", "label": "Scenic"}, {"key": "peaceful", "label": "Peaceful"}],
    "reported": [{"key": "good_views", "label": "Good views", "share": 0.404}]
  },
  "your_vibes": {"status": "emerging", "feedback_count": 1,
                 "liked_vibes": [{"key": "peaceful", "label": "Peaceful", "emoji": "🌿"}, {"key": "scenic", "label": "Scenic", "emoji": "🌅"}],
                 "avoids": [{"key": "too_crowded", "label": "Too crowded"}]},
  "message": "Thanks — this shapes what GeoGuide suggests next."
}
```

Validation errors return `422 {"detail": {"code": "invalid_feedback", "field": "custom_vibes", "message": "…"}}`.

---

## 2. Event discovery and local events intelligence

### Pipeline (`app/events/engine.py`)

```
intent (app/events/intent.py: categories, free, festivals, near me)
 → destination / "near me" (GeoContext + city resolver)
 → date range (app/core/dates.py: today, tonight, tomorrow, this/next weekend, this/next week,
               this month, "in October", "Oct 22", during my trip, upcoming) → aware datetimes in the destination's timezone
 → structured providers ─┬─ stored     curated packs + organiser dataset (dated records only)
                         ├─ ticketmaster  Discovery API: latlong+radius(km), UTC date range, keyword, classification, paging
                         └─ google_events SerpApi Google Events (+ its date chips)
 → web fallback (only when fewer than 5 events answer the request, or for festival searches):
       2–3 generated queries (topic/category × place × dates × season × festival) → single-event, dated pages only
 → normalise (app/events/model.py: NormalisedEvent) → validate dates (overlap, tonight = from 17:00)
 → link venues to known places (app/events/venues.py; no duplicate places) → geographic boundary
 → freshness (fresh / recent / stale / stored; expired events removed) → de-duplicate → confidence
 → filters (category, free, festival) → rank → top N
```

* **Geography.** Events are fetched for the **exploration destination**. The boundary is the destination's coverage + 5 km (at least 15 km), measured as a distance, not by matching city names. Events the stored data links to the city are always kept, while live and web results must fall inside the boundary. Physical location is used only for "near me" (`near=me`; 10 km by default), and then distances are measured from the traveller.
* **Seasons and festivals.** Festival searches add dedicated queries with month and season. A festival that is only *associated* with a city (it has typical months but no dates) is returned separately as "dates not confirmed", never as happening.

### Providers (`app/events/providers/`)

`EventProvider` defines `search_events(query) → ProviderResult`, `get_event(id)` and `health_check()`. Adding a provider means adding a class to `default_providers()`; ranking doesn't change. A provider that fails or throws is reported (`status: error`) and the others still answer. Past dates skip live providers (`not_applicable`).

**Country coverage.** `events.json → provider_coverage` lists the countries each provider actually serves; an empty list means everywhere. A provider outside its countries is skipped before any request is made and reported as `not_applicable` (`no_coverage`). Ticketmaster is limited to the markets it sells in (US, Canada, Mexico, UK, Ireland, Australia/NZ, much of Europe, UAE, South Africa), so it is **not used for India**. The country comes from the city's stored ISO code, or from its country name via `data/config/countries.json`.

**Local event platforms.** For countries listed in `events.json → country_sources`, the web fallback adds a search restricted to that country's ticketing and listing sites. India uses BookMyShow, District, Insider, Skillboxes, Townscript and AllEvents. None of these offers a public API, so their listings arrive through Google Events and site-restricted web search, and they go through the same single-event and date validation as any other page. Google Events is also localised with the city's country (`gl`).

| Provider | Needs | Reliability |
|---|---|---|
| Stored (curated / dataset) | nothing | 0.8 / 0.55 |
| Official calendars | files in `data/sources/festival_calendars/` | 0.85 |
| Reviewed submissions (stored) | `EVENT_MODERATOR_EMAILS` for reviewers | 0.7 |
| Ticketmaster | `TICKETMASTER_API_KEY`; only in covered countries (not India) | 0.9 |
| Google Events | `SERPAPI_KEY` | 0.65 |
| Web pages (+ country platforms such as BookMyShow and District) | `SERPAPI_KEY` | official 0.85 · event platform 0.7 · news 0.6 · aggregator 0.4 · unknown 0.35 |

Keys stay on the server. `GET /api/events/providers` reports only whether each provider is configured. `httpx` request logging is silenced because keys travel as query parameters.

**Official festival calendars** (`app/events/providers/calendar.py`). Each JSON file in `data/sources/festival_calendars/` is one published government calendar. It names a country and, optionally, the states it applies to, plus its authority, source link and verification date. Entries are either dated for one year (`"date": "2026-11-08"`) or fixed every year (`"annual": "11-01"`), and dates that depend on the moon are flagged so the card says the date may shift by a day. Calendar days apply to every city in that country or state, with no venue and no price, and the card links to the calendar. The files included are the Central Government gazetted holidays for 2026 (DoPT order of 3 July 2025), India's fixed national days, and Karnataka Rajyotsava. The 2026 dates were cross-checked against several published copies because the official PDF was not reachable from the build environment. Karnataka's 2026 festival list is left out because published copies disagree on some dates. To add a state, drop in another file with dates taken from its official order.

**Organiser submissions** (`app/events/submissions.py`, `POST /api/events/submissions`). A logged-in organiser, venue or attendee sends an event using "Know an event that's missing? Add it" under *What's happening*. It is validated before anyone reviews it:
- **Required fields:** a city from the stored list and a venue.
- **Dates:** real dates, no more than 60 days long, not already over, and at most about a year ahead.
- **Times:** 24-hour format.
- **Venue location:** coordinates, if given, must be in or near the city.
- **Price and links:** a price and a 3-letter currency when the event is paid; links must be http(s).

Near-identical titles on overlapping dates in the same city are rejected as duplicates, and each user can have at most 10 submissions pending. Nothing is shown until a moderator approves it. Moderators are the accounts listed in `EVENT_MODERATOR_EMAILS`, and they review in *Profile*, where rejecting requires a reason the submitter can see. An approved event becomes a stored event with the source "<organiser> (organiser submission, reviewed)". The contact email is only visible to moderators.

**Web validation.** A result becomes an event only if it is a single event (round-ups like "Top 10 events in…" are dropped), it has an explicit date inside the range (an unreadable date means the result is dropped, never guessed), and it names the destination or a venue. Price is classified as free, paid, donation or unknown from the page text.

### Location: which city you are in, and what "city centre" means

* **City extent** (`data/config/geo.json → city_extent`). A stored city's `coverage_radius_km` only measures how far its stored places spread (Bengaluru: 9.4 km). Location detection instead uses `max(coverage, 6 km × √population in millions)`, capped at 30 km (Bengaluru: about 22 km). Whitefield, Electronic City and Yelahanka are therefore recognised as Bengaluru. Retrieval radii are unchanged.
* **Aliases.** Common and historical names such as Bangalore, Bombay, Mysore, Trivandrum and Cochin resolve to the stored city, both in questions and when reverse geocoding returns a different name.
* **"City centre", "downtown", "the center"** mean the centre of the city you are exploring, or of the city your GPS is in. They are never geocoded. The radius is a quarter of the city's extent, kept between 2 and 6 km. Before this change, "near city center" went to Nominatim with no location bias, and its first worldwide match was a Tallinn district named "City Centre".
* **Nominatim is biased to you.** Names are looked up inside a box of about 60 km around the destination being explored, or around you, first. A match outside it is accepted only if it is a settlement or a well-known place (`importance ≥ 0.5`). Generic names ("bus stand", "railway station") are never searched worldwide. Results are requested in English.
* The context bar shows the city your GPS is in ("You · Bengaluru"). When a destination saved earlier is somewhere else, it offers "You're in Bengaluru · explore here".

### De-duplication (`app/events/quality.py`)

Two records are the same event when they share a provider ID or URL, or when their titles are close (after removing years, city names and "tickets"), their dates are the same or overlap, **and** their venues are compatible (coordinates within 1 km, or similar venue names). Similar names alone never merge. The merged record keeps the most reliable source as primary, fills gaps from the others, and lists every source.

### Confidence

```
0.45·source reliability + 0.15·explicit time + 0.1·venue + 0.1·coordinates + 0.1·corroboration + 0.1·freshness
```

It is labelled high (≥ 0.7), medium (≥ 0.5) or low, with notes ("Official source", "Reported by 2 sources", "Date only — check the time", "Listing not re-checked recently"). Low-confidence events are ranked with a ×0.8 penalty and flagged in the UI.

### Ranking (weights in `events.json → ranking`)

The weights are `time 0.2` (on now > soon), `distance 0.15`, `relevance 0.2` (category/keywords), `confidence 0.2`, `freshness 0.05`, `vibe 0.1` and `popularity 0.1` (expected footfall, when known).

The `vibe` component maps each event category to vibes (for example music → lively/social/youthful and exhibition → cultural/calm/aesthetic) and compares them with the traveller's feedback-derived preferences. It is one signal among several and never overrides relevance.

### Caching and freshness

* Ticketmaster responses are cached for 30 minutes, keyed by destination, centre, radius, time range, categories and keyword. SerpApi responses use the web-search cache.
* Freshness is recomputed on every read, so an event is never served after it ends.
* Live listings are no longer written into the stored events table; only curated and dataset records are stored.

### Observability

Each search logs one line (`events_search`) with the provider, status, raw and kept counts, drop reasons (no_date, not_an_event, cancelled…), latency, the removals from validation, geography, expiry and de-duplication, and the number returned. No secrets are logged.

### Events API

| Endpoint | |
|---|---|
| `GET /api/events` | `destination_id` or GPS; `date`, `when` (tonight, this weekend, next week, Oct 22, in October, during my trip) or `end`; `q`; `category`; `free`; `festival`; `near=me`; `radius_km`; `trip_start` / `trip_end` |
| `GET /api/events/overview` | The next 30 days in one retrieval, split into the tabs that have events: Happening today, Tonight, This weekend, Upcoming, Festivals, Cultural, Music, Food, Art, Family, Free |
| `GET /api/events/providers` | Provider configuration (no keys) |

Example: `GET /api/events?destination_id=dest-hampi&when=this weekend&category=music`, captured against local mock providers (one event shown, fields trimmed; the mock's odd venue wording in the title is shortened here):

```json
{
  "range": {"start": "2026-09-26", "end": "2026-09-27", "label": "Sat 26 – Sun 27 Sep 2026", "kind": "weekend",
            "start_time": "2026-09-26T00:00:00+05:30", "end_time": "2026-09-27T23:59:59+05:30"},
  "area": {"mode": "destination", "centre": {"lat": 15.335, "lon": 76.46}, "radius_km": 17.0},
  "event_status": "verified_events_found",
  "events": [{
    "name": "Indie Rock Night at Brew Yard", "category": "music", "type": "event",
    "start": "2026-09-26T19:00:00+05:30", "end": "2026-09-26T23:00:00+05:30", "start_time": "19:00",
    "venue": {"name": "Brew Yard", "place_id": null}, "price": {"kind": "unknown"},
    "event_url": "https://tickets.example/indie-rock",
    "source": {"name": "Google Events listing (tickets.example)", "provider": "google_events", "retrieved_at": "2026-09-24T08:03:52.584017+00:00"},
    "confidence": 0.642, "confidence_label": "medium", "freshness": "fresh", "reasons": ["Sat 26 Sep, 19:00", "Event listing"]
  }],
  "sources_checked": [
    {"provider": "stored", "status": "ok", "found": 1, "kept": 1},
    {"provider": "ticketmaster", "status": "ok", "found": 3, "kept": 3},
    {"provider": "google_events", "status": "ok", "found": 3, "kept": 2, "dropped": {"no_date": 1}}
  ],
  "counts": {"raw": 7, "normalised": 6, "out_of_range": 1, "outside_area": 0, "expired": 0, "duplicates": 0, "filtered": 4, "returned": 1}
}
```

No matches gives `"events": []`, `"event_status": "no_verified_events_found"` and a plain message; nothing is ever substituted.

---

## Tests

* `tests/test_feedback_vibes.py`:
  - peaceful/scenic vs lively/youthful travellers;
  - same geography with different vibe compatibility;
  - dislikes crowds; dislikes expensive places;
  - no history; a single review;
  - conflicting feedback; a new place without feedback;
  - a new vibe joining the vocabulary; malformed custom vibes;
  - a user's dislike vs the place's global quality;
  - text kept alongside derived signals; NLP negation;
  - validation; the API flow; the synthetic bootstrap, and excluding it.
* `tests/test_events_layer.py`:
  - tonight, weekend;
  - destination vs physical location; near me by distance;
  - missing coordinates and venue linking;
  - multi-provider de-duplication; similar names not merged;
  - stale and expired events; official vs aggregator confidence;
  - festival, category and free filters; price classification;
  - provider failure; no results;
  - web fallback validation and query generation; the fallback trigger;
  - vibe-personalised ranking;
  - Ticketmaster mapping and filters;
  - the API.
