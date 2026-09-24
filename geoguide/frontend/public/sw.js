/* GeoGuide service worker: offline app shell and saved copies of recently viewed data.
 *
 * - Page navigations: network first; the cached app shell is used only when offline
 *   (so a new release is never hidden behind a stale cache).
 * - Hashed build assets (/assets/*): cache first (their names change with every build).
 * - Read-only API endpoints: network first with a timeout; the last good answer is
 *   served when offline, marked with the X-GeoGuide-Offline header and when it was saved.
 * - Personal endpoints, writes and provider-backed look-ups that must be live are never cached.
 */
const VERSION = 'v2'
const BUILD = 'dev'  // replaced at build time with a hash of the asset list
const PRECACHE = []  // replaced at build time with every hashed JS/CSS file of the release
const SHELL_CACHE = `geoguide-shell-${VERSION}-${BUILD}`
const ASSET_CACHE = `geoguide-assets-${VERSION}-${BUILD}`
const API_CACHE = `geoguide-api-${VERSION}`
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icon-192.svg', '/icon-512.svg']
const API_TIMEOUT_MS = 6000
const API_MAX_ENTRIES = 250

// Data that is safe to show from a saved copy (not personal, not writes).
const CACHEABLE_API = [
  /^\/api\/config$/, /^\/api\/context(\/briefing)?$/, /^\/api\/events(\/overview)?$/, /^\/api\/nearby$/, /^\/api\/hotels$/,
  /^\/api\/search$/, /^\/api\/places\/[^/]+(\/similar|\/community)?$/, /^\/api\/destinations$/,
  /^\/api\/destinations\/[^/]+\/(places|knowledge|pack)$/, /^\/api\/weather$/, /^\/api\/feedback\/vocabulary$/,
]

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    await (await caches.open(SHELL_CACHE)).addAll(SHELL)
    // The whole app, so every screen works offline even if it was never opened online.
    if (PRECACHE.length) await (await caches.open(ASSET_CACHE)).addAll(PRECACHE)
    await self.skipWaiting()
  })())
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([SHELL_CACHE, ASSET_CACHE, API_CACHE])
    const names = await caches.keys()
    await Promise.all(names.filter((name) => name.startsWith('geoguide-') && !keep.has(name)).map((name) => caches.delete(name)))
    await self.clients.claim()
  })())
})

const withTimeout = (promise, ms) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('timeout')), ms)
  promise.then((value) => { clearTimeout(timer); resolve(value) }, (error) => { clearTimeout(timer); reject(error) })
})

async function trim(cacheName, max) {
  const cache = await caches.open(cacheName)
  const keys = await cache.keys()
  for (const key of keys.slice(0, Math.max(0, keys.length - max))) await cache.delete(key)
}

async function saveApi(request, response) {
  const body = await response.clone().blob()
  const headers = new Headers(response.headers)
  headers.set('X-GeoGuide-Saved-At', new Date().toISOString())
  const cache = await caches.open(API_CACHE)
  await cache.put(request, new Response(body, { status: response.status, statusText: response.statusText, headers }))
  trim(API_CACHE, API_MAX_ENTRIES)
}

async function fromSaved(request) {
  const cache = await caches.open(API_CACHE)
  const saved = await cache.match(request, { ignoreVary: true })
  if (!saved) return null
  const headers = new Headers(saved.headers)
  headers.set('X-GeoGuide-Offline', '1')
  return new Response(await saved.blob(), { status: saved.status, statusText: saved.statusText, headers })
}

async function apiNetworkFirst(event) {
  const { request } = event
  try {
    const response = await withTimeout(fetch(request), API_TIMEOUT_MS)
    if (response.ok) event.waitUntil(saveApi(request, response))
    return response
  } catch (error) {
    const saved = await fromSaved(request)
    if (saved) return saved
    throw error
  }
}

async function assetCacheFirst(request) {
  const cache = await caches.open(ASSET_CACHE)
  // Hashed assets never change; ignore Vary (module scripts carry an Origin header the precache fetch didn't).
  const hit = await cache.match(request, { ignoreVary: true, ignoreSearch: true })
  if (hit) return hit
  const response = await fetch(request)
  if (response.ok) cache.put(request, response.clone())
  return response
}

async function navigationNetworkFirst(request) {
  try {
    const response = await fetch(request)
    if (response.ok) (await caches.open(SHELL_CACHE)).put('/index.html', response.clone())
    return response
  } catch {
    return (await caches.match('/index.html', { ignoreVary: true })) || (await caches.match('/', { ignoreVary: true })) || Response.error()
  }
}

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (request.mode === 'navigate') {
    event.respondWith(navigationNetworkFirst(request))
  } else if (url.pathname.startsWith('/assets/')) {
    event.respondWith(assetCacheFirst(request))
  } else if (url.pathname.startsWith('/api/')) {
    if (CACHEABLE_API.some((pattern) => pattern.test(url.pathname))) event.respondWith(apiNetworkFirst(event))
  } else if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(request, { ignoreVary: true }).then((hit) => hit || fetch(request)))
  }
})

// The app can ask to forget every saved API answer (e.g. on log-out).
self.addEventListener('message', (event) => {
  if (event.data === 'clear-api-cache') event.waitUntil(caches.delete(API_CACHE))
})
