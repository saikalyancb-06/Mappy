import { Trash2 } from 'lucide-react'
import { deletePack } from '../offline/packs'
import { usePacks } from '../offline/useOffline'
import { SectionTitle } from './ui'

// Cities saved on this device for offline use.
export default function OfflineCities() {
  const packs = usePacks()
  if (!packs.length) return null
  const total = packs.reduce((sum, p) => sum + (p.size || 0), 0)
  return <>
    <SectionTitle eyebrow="Offline">Saved on this device · {Math.max(1, Math.round(total / 1024))} KB</SectionTitle>
    <div className="similar-list">{packs.map((pack) => <div key={pack.id} className="similar-item saved-row">
      <strong>{pack.name}</strong>
      <button type="button" className="chip" onClick={() => deletePack(pack.id)}><Trash2 size={14} /> Remove</button>
      <small>{pack.places} places · saved {new Date(pack.savedAt).toLocaleString()}</small>
    </div>)}</div>
  </>
}
