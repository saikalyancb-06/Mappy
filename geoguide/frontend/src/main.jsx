import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Offline support: the service worker is registered in production builds only; in development it would
// interfere with hot reloading, so any earlier registration is removed there.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    if (import.meta.env.PROD) {
      try { await navigator.serviceWorker.register('/sw.js') } catch { /* offline support is optional */ }
      return
    }
    const registrations = await navigator.serviceWorker.getRegistrations()
    await Promise.all(registrations.map((registration) => registration.unregister()))
    if ('caches' in window) {
      const cacheNames = await caches.keys()
      await Promise.all(cacheNames.filter((name) => name.startsWith('geoguide-')).map((name) => caches.delete(name)))
    }
  })
}
