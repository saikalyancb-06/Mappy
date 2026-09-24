# Offline use

GeoGuide keeps working without a connection in two ways.

## 1. Automatic: what this device has already seen

A service worker (`frontend/public/sw.js`) is registered in production builds only. In development it is removed, because it would break hot reloading.

| Request | Strategy |
|---|---|
| Page navigations | Network first; the cached app shell only when offline, so a new release is never hidden |
| App code (`/assets/*`, hashed) | Precached on install from the build's own file list, then cache first |
| Read-only data (`/api/context`, `/api/nearby`, `/api/events`, `/api/places/*`, `/api/search`, city places and knowledge, config …) | Network first with a 6 s timeout. When offline, the last good answer is returned, marked `X-GeoGuide-Offline` and with the time it was saved. At most 250 entries are kept. |
| Personal data, sign-in and all writes | Never cached |

A small Vite plugin (`vite.config.js`) writes the release's JS/CSS file list into `dist/sw.js`. Every screen therefore works offline even if it was never opened online, and every release changes the worker, so browsers update it. Caches from old releases are deleted on activation.

## 2. Explicit: "Save for offline"

On Explore, **Save for offline** downloads `GET /api/destinations/{id}/offline-pack`, a compact bundle stored in IndexedDB (a city with a few hundred places is roughly 10–200 KB, gzipped on the wire). It contains:

- **Places:** name, category, location, rating, contact, photo, and structured opening hours, so "open now" still works offline.
- **City knowledge:** overview, history, culture and so on, with sources.
- **Safety advisories** and **stored and official events** for the next 45 days. Live listings are not included.
- **The category taxonomy,** so "museums" and "cafes" filters work.

The same recommendation policy as online applies: closed places and the traveller's exclusions are left out.

When the network is unavailable and no saved copy exists for a request, the app answers on the device from the pack (`frontend/src/offline/`):

- **Explore:** the city overview, top attractions, saved events and advisories. There is no weather; the page says so.
- **Nearby and filters:** sorted by rating and distance, with "open now" from the saved hours.
- **Search:** by name or category.
- **Ask:** places, city knowledge (history, culture, food, transport) and saved events. Every answer says it comes from the saved guide, and when it was saved.

Saved packs refresh quietly when they are more than 3 days old and the device is online. Profile lists the cities saved on the device, with their size and a Remove button. Logging out deletes the saved packs and saved API answers, and the signed-in user is remembered so the app can start offline.
