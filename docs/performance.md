# Performance

## Method

The main endpoints were benchmarked against a local copy of the database, with mock providers configured to add realistic latencies:

| Provider | Added latency |
|---|---|
| SerpApi | 1.2 s |
| Groq LLM | 0.9 s |
| Open-Meteo | 0.3 s |
| Nominatim | 0.3 s |
| Wikipedia | 0.4 s |
| Ticketmaster | 0.5 s |

Times are wall-clock from an HTTP client. "Cold" means an empty cache and a fresh process. The Python hot paths were measured with `cProfile`. The benchmark script is `bench.py` in the scratch folder; the tables below are the numbers from those runs.

## What changed

| Area | Change | Effect |
|---|---|---|
| Explore page (`/api/context`) | Independent steps run concurrently: the weather→attractions chain, events, and both knowledge look-ups | Cold **6.5 s → 2.5 s** |
| Explore page | `briefing=deferred`: the page is served with a briefing built deterministically from verified data, and the AI briefing is fetched from `/api/context/briefing` | The page no longer waits about 0.9 s for the LLM |
| Events | Structured providers are queried in parallel; the web fallback's queries are sent together | Cold events: wait for the slowest source, not the sum |
| HTTP | One pooled, keep-alive `httpx` client for every provider, including the LLM | Repeat calls skip TCP/TLS setup (typically 100–300 ms per call to a real HTTPS API) |
| Query parsing | The `(?:^|\s)phrase(?:\s|$)` regexes were built per phrase and thrashed Python's regex cache; they are replaced by an exact whole-word string test, and `normalize()` is memoised | Ask-pipeline CPU **37 ms → 8 ms per question**; the test suite dropped from 7.4 s to about 5 s |
| City detection | `nearest_destination` uses a cached index of positions and extents. It is invalidated by ORM events and self-heals after bulk deletes | Removes several full `destinations` scans per request |
| Destination list | POI counts come from one `GROUP BY` instead of one query per city | 60 queries become 1 |
| Payloads | `GZipMiddleware` | Context 35 KB → 6 KB, nearby 36 KB → 4 KB on the wire |
| Database | Indexes on POIs by `(destination_id, category)`, `(destination_id, kind)`, `(destination_id, source, external_place_id)` and `(lat, lon)`, and on knowledge by `(destination_id, kind)` | Faster city-scoped look-ups |
| City intelligence | Once a city is prepared, questions are answered from the database. The live Maps search that most discovery questions used to trigger no longer runs | About −1.2 s per such question, and no SerpApi quota |
| Frontend | Client cache for read endpoints: identical in-flight requests are shared, results live 30–300 s, and the cache clears on writes and new city data | Returning to a tab or date is instant (about 60 ms) |
| Frontend | Secondary screens are code-split with `React.lazy` and prefetched when the browser is idle | Main bundle 336 KB → 290 KB; first tap on a tab is still instant |

## Measured (sandbox, realistic mock latency)

| Endpoint | Before (cold) | After (cold) | Warm |
|---|---|---|---|
| `/api/context` (Explore) | 6.5 s | 2.5 s (deferred) | 30 ms |
| `/api/events/overview` | 1.3 s | 1.2 s | 12 ms |
| `/api/ask` (famous places) | 1.0 s | 0.94 s (one LLM call) | 0.93 s |
| `/api/search` | 1.3 s | 1.2 s | 23 ms |

The remaining cold time is provider latency. The events web fallback deliberately runs only when fewer than 5 events are found, to save SerpApi quota, so a cold events request can take two provider round-trips. The ask endpoint's remaining time is the single grounded LLM call.
