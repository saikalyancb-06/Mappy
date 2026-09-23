const CACHE_NAME = 'geoguide-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/icon-192.svg', '/icon-512.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)))
})

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET' || event.request.url.includes('/api/')) return
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
    const copy = response.clone()
    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy))
    return response
  }).catch(() => caches.match('/'))))
})
