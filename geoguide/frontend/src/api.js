const request = async (path, options = {}) => {
  const token = localStorage.getItem('geoguide-token')
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
    ...options,
  })

  if (!response.ok) {
    let detail = ''
    try {
      const payload = await response.json()
      detail = typeof payload.detail === 'string' ? payload.detail : ''
    } catch {
      // Some failures do not include a JSON response body.
    }
    throw new Error(detail || `Request failed (${response.status})`)
  }

  return response.json()
}

export const signUp = (name, email, password) => request('/api/auth/signup', { method: 'POST', body: JSON.stringify({ name, email, password }) })
export const logIn = (email, password) => request('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
export const logOut = () => request('/api/auth/logout', { method: 'POST' })
export const getCurrentUser = () => request('/api/auth/me')
export const updatePreferences = (preferences) => request('/api/preferences', { method: 'PUT', body: JSON.stringify(preferences) })
export const recordInteraction = (event) => request('/api/interactions', { method: 'POST', body: JSON.stringify(event) })

const locationQuery = (location = {}) => {
  const params = new URLSearchParams()
  if (location.lat != null) params.set('lat', location.lat)
  if (location.lon != null) params.set('lon', location.lon)
  if (location.city) params.set('city', location.city)
  if (location.source) params.set('source', location.source)
  if (location.timestamp) params.set('timestamp', location.timestamp)
  if (location.accuracy_meters != null) params.set('accuracy_meters', location.accuracy_meters)
  return params.toString() ? `?${params.toString()}` : ''
}

export const getNow = (location) => request(`/api/now${locationQuery(location)}`)
export const getNearby = (location, mode = 'nearby', category = null) => {
  const query = locationQuery(location)
  const sep = query ? '&' : '?'
  const catParam = category ? `&category=${encodeURIComponent(category)}` : ''
  return request(`/api/nearby${query}${sep}mode=${encodeURIComponent(mode)}${catParam}`)
}
export const askGeoGuide = (question, locationContext, queryDestination = null) => request('/api/ask', {
  method: 'POST',
  body: JSON.stringify({ question, location_context: locationContext, query_destination: queryDestination }),
})

export const startLocationIngestion = (location) => request('/api/location', {
  method: 'POST',
  body: JSON.stringify(location),
})
export const getIngestionStatus = (jobId) => request(`/api/ingestion/${jobId}/events`)

export const normalizePlace = (place) => ({
  id: place.id || place.name || `place-${Math.random().toString(36).slice(2)}`,
  name: place.name || 'Unnamed place',
  category: place.category || 'Place',
  distance: place.distance_km == null ? null : `${Number(place.distance_km).toFixed(1)} km`,
  score: place.score == null ? null : Math.round(Number(place.score) * 100),
  reasons: Array.isArray(place.reasons) ? place.reasons : [],
  image: place.image_url || place.image || null,
  lat: place.lat ?? null,
  lon: place.lon ?? null,
  source: place.source || null,
  openingHours: place.opening_hours || null,
  address: place.address || null,
  rating: place.rating == null ? null : Number(place.rating),
})
