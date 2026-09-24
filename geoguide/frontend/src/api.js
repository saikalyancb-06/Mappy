// Thin client for the GeoGuide API. No keys or provider calls live in the frontend:
// every external service (LLM, web search, weather, maps) is reached through the backend.

const TOKEN_KEY = 'geoguide-token'

export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

// ---- short-lived client cache for read endpoints ----
// Identical requests in flight are shared, and recent answers are reused, so switching tabs or dates back and
// forth is instant. Keys include the signed-in user; any write (and new city data) clears the cache.
const CACHE_TTL_MS = [
  ['/api/config', 3600000], ['/api/feedback/vocabulary', 3600000], ['/api/context/briefing', 300000],
  ['/api/context', 60000], ['/api/events', 60000], ['/api/nearby', 30000], ['/api/hotels', 60000],
  ['/api/places/', 30000], ['/api/search', 30000],
]
const cache = new Map()
const ttlFor = (path) => {
  if (/^\/api\/destinations(\?|$)/.test(path)) return 300000
  if (/^\/api\/destinations\/[^/]+\/(places|knowledge)/.test(path)) return 60000
  const hit = CACHE_TTL_MS.find(([prefix]) => path.startsWith(prefix))
  return hit ? hit[1] : 0
}
export const clearApiCache = () => cache.clear()

const request = async (path, options = {}) => {
  const method = (options.method || 'GET').toUpperCase()
  if (method !== 'GET') {
    if (!['/api/interactions', '/api/ask', '/api/plan/deck', '/api/destinations/ensure'].some((prefix) => path.startsWith(prefix))) cache.clear()
    return send(path, options)
  }
  const ttl = ttlFor(path)
  if (!ttl) return send(path, options)
  const key = `${getToken() || ''}|${path}`
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < ttl) return hit.promise
  const promise = send(path, options)
  cache.set(key, { at: Date.now(), promise })
  promise.catch(() => cache.delete(key))  // never cache failures
  if (cache.size > 300) cache.delete(cache.keys().next().value)
  return promise
}

const send = async (path, options = {}) => {
  const token = getToken()
  let response
  try {
    response = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
    })
  } catch {
    throw new ApiError('GeoGuide is unreachable. Check your connection and that the backend is running.', 0)
  }
  if (!response.ok) {
    let detail = null
    try { detail = (await response.json()).detail } catch { /* non-JSON error body */ }
    const message = typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`
    throw new ApiError(message, response.status, detail)
  }
  return response.json()
}

// ---- context params: physical location and active destination are sent separately ----
export const contextParams = ({ userLocation, destination } = {}, extra = {}) => {
  const params = new URLSearchParams()
  if (userLocation) {
    params.set('lat', userLocation.lat)
    params.set('lon', userLocation.lon)
    if (userLocation.accuracy_m != null) params.set('accuracy_m', userLocation.accuracy_m)
    params.set('timestamp', userLocation.timestamp)
  }
  if (destination?.destination_id) params.set('destination_id', destination.destination_id)
  else if (destination?.name) params.set('destination', destination.name)
  Object.entries(extra).forEach(([key, value]) => { if (value !== null && value !== undefined && value !== '') params.set(key, value) })
  const text = params.toString()
  return text ? `?${text}` : ''
}

const destinationBody = (destination) => {
  if (!destination) return null
  if (destination.destination_id) return { id: destination.destination_id }
  return { name: destination.name, lat: destination.lat, lon: destination.lon, coverage_radius_km: destination.coverage_radius_km }
}

// ---- auth & profile ----
export const signUp = (name, email, password) => request('/api/auth/signup', { method: 'POST', body: JSON.stringify({ name, email, password }) })
export const logIn = (email, password) => request('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
export const logOut = () => request('/api/auth/logout', { method: 'POST' })
export const getCurrentUser = () => request('/api/auth/me')
export const getPreferences = () => request('/api/preferences')
export const updatePreferences = (preferences) => request('/api/preferences', { method: 'PUT', body: JSON.stringify(preferences) })
export const recordInteraction = (event) => request('/api/interactions', { method: 'POST', body: JSON.stringify(event) })

// ---- configuration & destinations ----
export const getConfig = () => request('/api/config')
export const getHealth = () => request('/api/health')
export const listDestinations = (q = '', near = null, limit = 8) => {
  const params = new URLSearchParams({ limit: String(limit) })
  if (q) params.set('q', q)
  if (near) { params.set('lat', String(near.lat)); params.set('lon', String(near.lon)) }
  return request(`/api/destinations?${params}`)
}
export const resolveDestination = (q) => request(`/api/destinations/resolve?q=${encodeURIComponent(q)}`)
export const describeLocation = (userLocation) => request(`/api/location/describe${contextParams({ userLocation })}`)
export const prefetchArea = (context) => request('/api/destinations/prefetch', { method: 'POST', body: JSON.stringify({ ...(context.userLocation || {}), destination_id: context.destination?.destination_id, name: context.destination?.destination_id ? null : context.destination?.name }) })
export const getPrefetchStatus = (jobId) => request(`/api/destinations/prefetch/${jobId}`)
// City intelligence: selecting a city registers it and prepares its guide in the background.
export const ensureCity = (place) => request('/api/destinations/ensure', { method: 'POST', body: JSON.stringify(place.destination_id ? { destination_id: place.destination_id } : { name: place.name, lat: place.lat, lon: place.lon, country: place.country, region: place.region }) })
export const getCityStatus = (destinationId) => request(`/api/destinations/${encodeURIComponent(destinationId)}/status`)

// ---- screens ----
export const getNow = (context, language = 'en') => request(`/api/now${contextParams(context, { language })}`)
// City + date context: changing the date re-runs events, weather, season, tips and the briefing.
export const getCityContext = (context, { date, language = 'en' } = {}) => request(`/api/context${contextParams(context, { date, language, briefing: 'deferred' })}`)
export const getCityBriefing = (context, { date, language = 'en' } = {}) => request(`/api/context/briefing${contextParams(context, { date, language })}`)
export const getEvents = (context, { date, when, end, q, category, free, festival, near, radiusKm } = {}) => request(`/api/events${contextParams(context, { date, when, end, q, category, free: free ? 'true' : null, festival: festival ? 'true' : null, near, radius_km: radiusKm })}`)
export const getEventsOverview = (context, { date, near } = {}) => request(`/api/events/overview${contextParams(context, { date, near })}`)

// ---- event submissions (reviewed before publishing) ----
export const submitEvent = (submission) => request('/api/events/submissions', { method: 'POST', body: JSON.stringify(submission) })
export const getMySubmissions = () => request('/api/events/submissions/mine')
export const getReviewQueue = () => request('/api/events/submissions/review')
export const reviewSubmission = (id, decision, note) => request(`/api/events/submissions/${encodeURIComponent(id)}/review`, { method: 'POST', body: JSON.stringify({ decision, note }) })

// ---- feedback & vibes ----
export const getFeedbackVocabulary = () => request('/api/feedback/vocabulary')
export const submitFeedback = (feedback) => request('/api/feedback', { method: 'POST', body: JSON.stringify(feedback) })
export const getPlaceCommunity = (placeId) => request(`/api/places/${encodeURIComponent(placeId)}/community`)
export const getMyVibes = () => request('/api/me/vibes')
export const getNearby = (context, { origin = 'auto', category, group, openNow, radiusKm, text, rankingMode, travelMode, withinBudget } = {}) => request(`/api/nearby${contextParams(context, { origin, category, group, open_now: openNow ? 'true' : null, radius_km: radiusKm, text, ranking_mode: rankingMode, travel_mode: travelMode, within_budget: withinBudget ? 'true' : null })}`)
export const getHotels = (context, { origin = 'auto', sort = 'best', checkIn, nights, maxPrice, minStars, withinBudget } = {}) => request(`/api/hotels${contextParams(context, { origin, sort, check_in: checkIn, nights, max_price: maxPrice, min_stars: minStars, within_budget: withinBudget ? 'true' : null })}`)
export const searchPlaces = (context, q, kind = null) => request(`/api/search${contextParams(context, { q, kind })}`)
export const getSimilarPlaces = (id) => request(`/api/places/${encodeURIComponent(id)}/similar`)
export const getPlace = (id, context) => request(`/api/places/${encodeURIComponent(id)}${contextParams({ userLocation: context?.userLocation })}`)

export const askGeoGuide = ({ question, context, selectedPlaceId, language, debug, date }) => request('/api/ask', {
  method: 'POST',
  body: JSON.stringify({
    question,
    user_location: context.userLocation || null,
    active_destination: destinationBody(context.destination),
    selected_place_id: selectedPlaceId || null,
    language,
    debug: Boolean(debug),
    date: date || null,
  }),
})

// Swipe deck: the candidate places for the wishes. Right-swipes are sent back as lockedIds, left-swipes as excludedIds.
export const getPlanDeck = ({ context, wishes, dayOffset, lockedIds, excludedIds, profile }) => request('/api/plan/deck', {
  method: 'POST',
  body: JSON.stringify({ user_location: context.userLocation || null, active_destination: destinationBody(context.destination), wishes: wishes || null, day_offset: dayOffset, locked_ids: lockedIds, excluded_ids: excludedIds, profile: profile || null }),
})

export const buildPlan = ({ context, duration, preset, dayOffset, start, lockedIds, excludedIds, previous, wishes, replan, profile }) => request('/api/plan', {
  method: 'POST',
  body: JSON.stringify({
    user_location: context.userLocation || null,
    active_destination: destinationBody(context.destination),
    duration,
    preset,
    day_offset: dayOffset,
    start,
    locked_ids: lockedIds,
    excluded_ids: excludedIds || [],
    previous,
    wishes: wishes || null,
    replan: replan || null,
    profile: profile || null,
  }),
})
