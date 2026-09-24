// Offline city packs: download a city once, then explore it with no connection.
import { idbDelete, idbGet, idbKeys, idbSet } from './db'

const listeners = new Set()
const notify = () => listeners.forEach((fn) => fn())
export const onPacksChanged = (fn) => { listeners.add(fn); return () => listeners.delete(fn) }

let memory = {} // destination_id -> pack (loaded lazily)

export const getPack = async (destinationId) => {
  if (!destinationId) return null
  if (memory[destinationId]) return memory[destinationId]
  try {
    const pack = await idbGet(destinationId)
    if (pack) memory[destinationId] = pack
    return pack || null
  } catch { return null }
}

export const listPacks = async () => {
  try {
    const keys = await idbKeys()
    const packs = await Promise.all(keys.map((key) => getPack(key)))
    return packs.filter(Boolean).map((pack) => ({ id: pack.city.id, name: pack.city.name, savedAt: pack.saved_at, places: pack.places.length, size: pack.size_bytes, version: pack.version }))
  } catch { return [] }
}

export const savePack = async (pack) => {
  const text = JSON.stringify(pack)
  const stored = { ...pack, saved_at: new Date().toISOString(), size_bytes: text.length }
  await idbSet(pack.city.id, stored)
  memory[pack.city.id] = stored
  notify()
  return stored
}

export const deletePack = async (destinationId) => {
  await idbDelete(destinationId)
  delete memory[destinationId]
  notify()
}

export const clearPacks = async () => {
  const keys = await idbKeys().catch(() => [])
  await Promise.all(keys.map((key) => idbDelete(key)))
  memory = {}
  notify()
}

// Try the pack of the given city first, then any saved pack that covers the point.
export const findPack = async ({ destinationId, point } = {}) => {
  const direct = await getPack(destinationId)
  if (direct) return direct
  if (!point) return null
  const keys = await idbKeys().catch(() => [])
  for (const key of keys) {
    const pack = await getPack(key)
    if (pack && distanceKm(point, pack.city) <= Math.max(25, pack.city.coverage_radius_km || 0)) return pack
  }
  return null
}

export const distanceKm = (a, b) => {
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLon = (b.lon - a.lon) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2
  return 2 * 6371 * Math.asin(Math.sqrt(h))
}
