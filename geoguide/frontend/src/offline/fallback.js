// Answer an API request from a saved city pack when the network is unavailable.
import { offlineAsk, offlineContext, offlineEvents, offlineNearby, offlineSearch } from './local'
import { findPack, getPack } from './packs'

const packFor = async (params, body) => {
  const destinationId = params.get('destination_id') || body?.active_destination?.id || body?.active_destination?.destination_id || body?.destination_id
  const lat = Number(params.get('lat') ?? body?.user_location?.lat)
  const lon = Number(params.get('lon') ?? body?.user_location?.lon)
  const point = Number.isFinite(lat) && Number.isFinite(lon) && (params.get('lat') || body?.user_location) ? { lat, lon } : null
  const pack = destinationId ? await getPack(destinationId) : await findPack({ point })
  return { pack: pack || (point ? await findPack({ point }) : null), point }
}

export async function offlineFallback(path, options = {}) {
  const url = new URL(path, 'http://offline.local')
  const params = url.searchParams
  const body = options.body ? (() => { try { return JSON.parse(options.body) } catch { return null } })() : null
  const route = url.pathname
  if (!['/api/nearby', '/api/search', '/api/ask', '/api/context', '/api/context/briefing', '/api/events/overview', '/api/events'].includes(route) && !/^\/api\/destinations\/[^/]+\/places$/.test(route)) return null
  const { pack, point } = await packFor(params, body)
  if (!pack) return null
  const useGps = params.get('origin') === 'user' || params.get('origin') === 'auto'
  switch (route) {
    case '/api/nearby':
      return offlineNearby(pack, { point: useGps ? point : null, category: params.get('category'), group: params.get('group'), openNow: params.get('open_now') === 'true', text: params.get('text') })
    case '/api/search':
      return offlineSearch(pack, params.get('q') || '')
    case '/api/ask':
      return offlineAsk(pack, body?.question || '', point)
    case '/api/context':
    case '/api/context/briefing':
      return offlineContext(pack, params.get('date'))
    case '/api/events/overview':
    case '/api/events':
      return offlineEvents(pack, params.get('date'))
    default:
      return { items: offlineNearby(pack, { category: params.get('category'), limit: 100 }).items, total: pack.places.length, offline: true }
  }
}
