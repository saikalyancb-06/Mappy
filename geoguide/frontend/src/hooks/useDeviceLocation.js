import { useCallback, useEffect, useRef, useState } from 'react'

// Continuous device location with explicit status, accuracy and timestamp.
// Nothing here ever substitutes a default place: without a fix, location is null.
const SESSION_KEY = 'geoguide-last-fix'
const MAX_REUSE_MS = 10 * 60 * 1000

const readSessionFix = () => {
  try {
    const fix = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null')
    return fix && Date.now() - Date.parse(fix.timestamp) < MAX_REUSE_MS ? fix : null
  } catch {
    return null
  }
}

export function useDeviceLocation() {
  const supported = typeof navigator !== 'undefined' && 'geolocation' in navigator
  const [location, setLocation] = useState(readSessionFix)
  const [status, setStatus] = useState(supported ? 'idle' : 'unsupported') // idle | locating | active | denied | unavailable | unsupported
  const [error, setError] = useState('')
  const watchId = useRef(null)

  const stop = useCallback(() => {
    if (watchId.current != null && supported) navigator.geolocation.clearWatch(watchId.current)
    watchId.current = null
  }, [supported])

  const start = useCallback(() => {
    if (!supported) { setStatus('unsupported'); return }
    stop()
    setStatus((current) => (current === 'active' ? current : 'locating'))
    setError('')
    watchId.current = navigator.geolocation.watchPosition(
      (position) => {
        const fix = {
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracy_m: Math.round(position.coords.accuracy),
          timestamp: new Date(position.timestamp || Date.now()).toISOString(),
          source: 'device',
        }
        setLocation(fix)
        setStatus('active')
        try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(fix)) } catch { /* storage unavailable */ }
      },
      (positionError) => {
        if (positionError.code === positionError.PERMISSION_DENIED) {
          setStatus('denied')
          setLocation(null)
          setError('Location permission was denied. You can still explore by choosing a destination.')
        } else {
          setStatus('unavailable')
          setError(positionError.message || 'Your location could not be determined right now.')
        }
      },
      { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 },
    )
  }, [stop, supported])

  const forget = useCallback(() => {
    stop()
    setLocation(null)
    setStatus(supported ? 'idle' : 'unsupported')
    try { sessionStorage.removeItem(SESSION_KEY) } catch { /* storage unavailable */ }
  }, [stop, supported])

  // Resume watching automatically when permission was already granted earlier.
  useEffect(() => {
    let cancelled = false
    if (!supported || !navigator.permissions?.query) return undefined
    navigator.permissions.query({ name: 'geolocation' }).then((permission) => {
      if (!cancelled && permission.state === 'granted') start()
      if (!cancelled && permission.state === 'denied') setStatus('denied')
    }).catch(() => {})
    return () => { cancelled = true }
  }, [start, supported])

  useEffect(() => stop, [stop])

  return { location, status, error, start, stop: forget }
}

export const describeFreshness = (location) => {
  if (!location) return null
  const minutes = Math.floor((Date.now() - Date.parse(location.timestamp)) / 60000)
  return minutes < 1 ? 'just now' : `${minutes} min ago`
}
