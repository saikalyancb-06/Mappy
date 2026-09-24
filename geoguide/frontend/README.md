# GeoGuide frontend

React + Vite PWA. See the repository root README for setup and the demo walkthrough.

* `src/api.js` – the only place that talks to the backend (`/api`, proxied to :8000 by Vite). No provider keys or external calls live in the frontend.
* `src/hooks/useDeviceLocation.js` – continuous GPS with accuracy, timestamp and explicit status; never substitutes a default location.
* `src/App.jsx` – app state: session, preferences, the active destination and the device location, kept separate.
* `src/views/*` – Now, Nearby, Place detail, Plan, Ask, Profile, Start.
* Option lists (interests, categories, languages, plan presets) come from `GET /api/config`.

```bash
npm install
npm run dev      # http://localhost:5173
npm run lint
npm run build
```
