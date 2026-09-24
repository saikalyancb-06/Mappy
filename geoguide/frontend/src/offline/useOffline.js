import { useCallback, useEffect, useState } from 'react'
import { getOfflinePack } from '../api'
import { deletePack, getPack, listPacks, onPacksChanged, savePack } from './packs'

export function useOnline() {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false))
  useEffect(() => {
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down) }
  }, [])
  return online
}

export function usePacks() {
  const [packs, setPacks] = useState([])
  const refresh = useCallback(() => listPacks().then(setPacks), [])
  useEffect(() => {
    const t = window.setTimeout(refresh, 0)
    const off = onPacksChanged(refresh)
    return () => { window.clearTimeout(t); off() }
  }, [refresh])
  return packs
}

// Save / update / remove the offline pack of one city.
export function useCityPack(destinationId) {
  const packs = usePacks()
  const info = packs.find((p) => p.id === destinationId) || null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = useCallback(async () => {
    if (!destinationId) return
    setBusy(true); setError('')
    try { await savePack(await getOfflinePack(destinationId)) } catch (e) { setError(e.message || 'Could not save this city.') } finally { setBusy(false) }
  }, [destinationId])
  const remove = useCallback(() => destinationId && deletePack(destinationId), [destinationId])
  return { info, busy, error, save, remove }
}

// Refresh a saved pack quietly when it is older than `maxAgeDays` and the device is online.
export async function refreshStalePack(destinationId, maxAgeDays = 3) {
  const pack = await getPack(destinationId)
  if (!pack || navigator.onLine === false) return
  if (Date.now() - Date.parse(pack.saved_at) < maxAgeDays * 86400000) return
  try { await savePack(await getOfflinePack(destinationId)) } catch { /* try again next time */ }
}
