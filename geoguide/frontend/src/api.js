const request = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })

  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`)
  }

  return response.json()
}

const locationQuery = (location = {}) => {
  const params = new URLSearchParams()
  if (location.lat != null) params.set('lat', location.lat)
  if (location.lon != null) params.set('lon', location.lon)
  if (location.city) params.set('city', location.city)
  return params.toString() ? `?${params.toString()}` : ''
}

export const getNow = (location) => request(`/api/now${locationQuery(location)}`)
export const getNearby = (location) => request(`/api/nearby${locationQuery(location)}`)
export const askGeoGuide = (question, location) => request('/api/ask', {
  method: 'POST',
  body: JSON.stringify({ question, location }),
})

export const startLocationIngestion = (location) => request('/api/location', {
  method: 'POST',
  body: JSON.stringify(location),
})

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
})
