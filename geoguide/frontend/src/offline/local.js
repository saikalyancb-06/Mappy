// On-device answers from a saved city pack, used when the network is unavailable.
// They mirror the server's response shapes so every screen renders unchanged, and they say
// plainly that the data is a saved copy (no live weather, no live listings).
import { distanceKm } from './packs'

const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

export const normalize = (text) => String(text || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/&/g, ' and ').replace(/'/g, '').match(/[a-z0-9]+/g)?.join(' ') || ''
const hasPhrase = (norm, phrase) => { const target = normalize(phrase); return Boolean(target) && ` ${norm} `.includes(` ${target} `) }

// Local clock in the city's timezone.
const cityClock = (timezone, at = new Date()) => {
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: timezone || undefined, weekday: 'short', hour: '2-digit', minute: '2-digit', year: 'numeric', month: '2-digit', day: '2-digit', hourCycle: 'h23' }).formatToParts(at).map((p) => [p.type, p.value]))
    return { day: parts.weekday.slice(0, 3).toLowerCase(), time: `${parts.hour}:${parts.minute}`, date: `${parts.year}-${parts.month}-${parts.day}` }
  } catch {
    return { day: DAYS[at.getDay()], time: at.toTimeString().slice(0, 5), date: at.toISOString().slice(0, 10) }
  }
}

// Same rules as the server's opening-hours evaluation; unknown hours stay unknown, never "open".
export const openStatus = (hours, clock) => {
  if (!hours) return 'unknown'
  if (hours.always_open) return 'open'
  const weekly = hours.weekly
  if (!weekly) return 'unknown'
  if ((hours.closed || []).includes(clock.day)) return 'closed'
  const spans = weekly[clock.day] ?? weekly.daily
  if (!spans) return 'closed'
  return spans.some(([start, end]) => start <= clock.time && clock.time < end) ? 'open' : 'closed'
}

const offlineSource = (pack, place) => [{ source_type: 'offline', source: `${place.source || 'GeoGuide'} · saved ${pack.saved_at?.slice(0, 10) || ''}`.trim(), source_url: null }]

const asCandidate = (pack, place, origin, clock) => {
  const distance = origin ? distanceKm(origin, place) : null
  const status = openStatus(place.opening_hours, clock)
  return {
    ...place, destination_id: pack.city.id, distance_km: distance != null ? Math.round(distance * 1000) / 1000 : null, open_status: status,
    open_detail: {}, sources: offlineSource(pack, place), conflicts: [], reasons: [
      distance != null ? `${distance < 1 ? `${Math.round(distance * 1000)} m` : `${distance.toFixed(1)} km`} away` : null,
      place.rating ? `Rated ${place.rating}${place.review_count ? ` (${place.review_count.toLocaleString()} reviews)` : ''}` : null,
      status === 'open' ? 'Open now (saved hours)' : null,
    ].filter(Boolean),
  }
}

const categoriesFor = (pack, { category, group } = {}) => {
  if (category) return new Set([category])
  if (group && pack.taxonomy?.groups?.[group]) return new Set(pack.taxonomy.groups[group].categories)
  return null
}

// Category named in free text ("museums", "cafes"), using the pack's taxonomy synonyms.
export const categoryInText = (pack, norm) => {
  let best = null
  for (const [id, entry] of Object.entries(pack.taxonomy?.categories || {})) {
    for (const word of [entry.label, ...(entry.synonyms || [])]) {
      const target = normalize(word)
      if (target && hasPhrase(norm, target) && (!best || target.length > best[0])) best = [target.length, id]
    }
  }
  return best?.[1] || null
}

const score = (place) => (place.rating || 3.5) * Math.log10(10 + (place.review_count || 0))

export function offlineNearby(pack, { point, category, group, openNow, text, limit = 30, kinds } = {}) {
  const clock = cityClock(pack.city.timezone)
  const origin = point || { lat: pack.city.lat, lon: pack.city.lon }
  const wanted = categoriesFor(pack, { category, group }) || (text ? (categoryInText(pack, normalize(text)) ? new Set([categoryInText(pack, normalize(text))]) : null) : null)
  const words = normalize(text).split(' ').filter((w) => w.length > 2)
  const items = pack.places
    .filter((p) => (!wanted || wanted.has(p.category)) && (!kinds || kinds.includes(p.kind)) && p.kind !== 'stay')
    .map((p) => asCandidate(pack, p, origin, clock))
    .filter((p) => !openNow || p.open_status === 'open')
    .filter((p) => !words.length || wanted || words.some((w) => normalize(`${p.name} ${p.category} ${p.description || ''}`).includes(w)))
    .sort((a, b) => (score(b) - (b.distance_km || 0) * 0.15) - (score(a) - (a.distance_km || 0) * 0.15))
    .slice(0, limit)
  return {
    items, offline: true, provider_errors: [],
    geo_context: { reference: { origin: point ? 'user_location' : 'active_destination', label: point ? 'your location' : pack.city.name, radius_km: pack.city.coverage_radius_km || 10 } },
    notices: [offlineNotice(pack)],
  }
}

export function offlineSearch(pack, query) {
  const norm = normalize(query)
  const category = categoryInText(pack, norm)
  const clock = cityClock(pack.city.timezone)
  const origin = { lat: pack.city.lat, lon: pack.city.lon }
  const places = pack.places
    .filter((p) => (category && p.category === category) || normalize(p.name).includes(norm) || norm.split(' ').every((w) => normalize(`${p.name} ${p.address || ''}`).includes(w)))
    .map((p) => asCandidate(pack, p, origin, clock))
    .sort((a, b) => score(b) - score(a))
    .slice(0, 12)
  return { places, destinations: [], category, provider_errors: [], offline: true }
}

const KNOWLEDGE_WORDS = { history: ['history', 'historical', 'founded', 'built', 'past'], culture: ['culture', 'cultural', 'festival', 'tradition', 'customs', 'people', 'language'], food: ['food', 'cuisine', 'eat', 'dish', 'dishes'], transport: ['transport', 'bus', 'metro', 'train', 'airport', 'getting around'], geography: ['geography', 'climate', 'weather', 'hills', 'river'], overview: ['about', 'known for', 'famous for', 'overview'] }
const EVENT_WORDS = ['event', 'events', 'happening', 'festival', 'festivals', 'tonight', 'weekend', 'concert', 'holiday', 'holidays']

export const offlineNotice = (pack) => `You're offline — showing ${pack.city.name} data saved on ${new Date(pack.saved_at || pack.generated_at).toLocaleString()}. Live weather, prices and new listings need a connection.`

export function offlineAsk(pack, question, point) {
  const norm = normalize(question)
  const base = { offline: true, intent: { intent: 'OFFLINE' }, answer_meta: { mode: 'offline' }, sources: [], understood: [], notices: [offlineNotice(pack)], confidence: 0.5, provider_errors: [], geo_context: { reference: { label: pack.city.name } } }
  if (EVENT_WORDS.some((w) => hasPhrase(norm, w))) {
    const events = (pack.events || []).slice(0, 6)
    const lines = events.map((e) => `- **${e.name}** — ${e.start_date}${e.end_date && e.end_date !== e.start_date ? ` to ${e.end_date}` : ''}${e.venue?.name ? `, ${e.venue.name}` : ''}`)
    return { ...base, events, results: [], answer: events.length ? `From the saved listings for ${pack.city.name} (live listings need a connection):\n${lines.join('\n')}` : `No saved events for ${pack.city.name} in the coming weeks. Live listings need a connection.` }
  }
  const topic = Object.entries(KNOWLEDGE_WORDS).find(([, words]) => words.some((w) => hasPhrase(norm, w)))?.[0]
  const category = categoryInText(pack, norm)
  if (topic && !category) {
    const docs = (pack.knowledge || []).filter((k) => k.type === topic || normalize(`${k.title} ${k.type}`).includes(topic))
    const doc = docs[0] || (pack.knowledge || []).find((k) => k.type === 'overview')
    if (doc) return { ...base, results: [], sources: [{ id: 'E1', title: doc.title, source: doc.source, url: doc.source_url }], answer: `${doc.content.split(/(?<=[.!?])\s+/).slice(0, 4).join(' ')} [E1]` }
  }
  const nearby = offlineNearby(pack, { point, category, text: category ? null : question, openNow: hasPhrase(norm, 'open now'), limit: 6, kinds: category ? null : ['attraction', 'activity'] })
  const results = nearby.items
  const answer = results.length
    ? `From your saved ${pack.city.name} guide:\n${results.slice(0, 5).map((p) => `- **${p.name}**${p.category ? ` (${p.category})` : ''}${p.reasons[0] ? ` — ${p.reasons[0]}` : ''}`).join('\n')}`
    : `I couldn't find that in the saved ${pack.city.name} guide. Try again when you're back online.`
  return { ...base, results, answer }
}

export function offlineEvents(pack, dateText) {
  const today = cityClock(pack.city.timezone).date
  const from = dateText || today
  const events = (pack.events || []).filter((e) => (e.end_date || e.start_date) >= from)
  return {
    city: { ...pack.city, key: pack.city.id, label: pack.city.name }, offline: true, events, message: events.length ? offlineNotice(pack) : `No saved events for ${pack.city.name}. ${offlineNotice(pack)}`,
    associated_festivals: [], range: { start: from, end: pack.events_until, label: `until ${pack.events_until}` }, area: { mode: 'destination', radius_km: pack.city.coverage_radius_km || 10 },
    sources_checked: [{ provider: 'offline', source: 'Saved offline pack', status: 'ok', found: events.length, kept: events.length }],
    tabs: events.length ? [{ id: 'upcoming', label: 'Saved upcoming', count: events.length, event_ids: events.map((e) => e.id) }] : [],
  }
}

export function offlineContext(pack, dateText) {
  const clock = cityClock(pack.city.timezone)
  const selected = dateText || clock.date
  const origin = { lat: pack.city.lat, lon: pack.city.lon }
  const knowledge = (types) => (pack.knowledge || []).filter((k) => types.includes(k.type)).map((k) => ({ chunk_id: k.id, title: k.title, content: k.content, category: k.type, source: k.source, source_url: k.source_url }))
  const attractions = pack.places.filter((p) => ['attraction', 'activity'].includes(p.kind)).map((p) => asCandidate(pack, p, origin, clock)).sort((a, b) => score(b) - score(a)).slice(0, 8)
  const events = offlineEvents(pack, selected)
  const overview = (pack.knowledge || []).find((k) => k.type === 'overview')
  const text = [overview ? overview.content.split(/(?<=[.!?])\s+/).slice(0, 2).join(' ') : pack.city.summary, attractions.length ? `Saved highlights: ${attractions.slice(0, 3).map((a) => a.name).join(', ')}.` : null, offlineNotice(pack)].filter(Boolean).join(' ')
  const days = Math.round((Date.parse(selected) - Date.parse(clock.date)) / 86400000)
  return {
    offline: true, city: { ...pack.city, key: pack.city.id, label: pack.city.name },
    date: { selected, today: clock.date, days_from_today: days, kind: days === 0 ? 'today' : days > 0 ? 'future' : 'past', label: selected },
    local_time: clock.time, season: null, weather: { status: 'unavailable', error: { source: 'offline', code: 'offline', message: 'Weather needs a connection.' } },
    events, advisories: pack.advisories || [], about: knowledge(['overview', 'history', 'geography', 'attractions']), culture: knowledge(['culture', 'food']),
    attractions, tips: [], briefing: { text, mode: 'deterministic', sources: [] }, data_status: 'offline', provider_errors: [],
  }
}
