import { CloudDownload, CloudOff, RefreshCw, Trash2 } from 'lucide-react'
import { useCityPack } from '../offline/useOffline'

const size = (bytes) => bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`

// "Save for offline": keep a city's places, guide, events and safety notes on this device.
export default function OfflineCard({ destination, online }) {
  const { info, busy, error, save, remove } = useCityPack(destination?.destination_id)
  if (!destination?.destination_id) return null
  if (!info) {
    return <section className="offline-card">
      <div><strong><CloudDownload size={16} /> Use {destination.name} offline</strong><p>Save places, the city guide, upcoming events and safety notes on this device ({online ? 'about 10–200 KB' : 'needs a connection'}).</p></div>
      <button type="button" className="secondary-button small" disabled={busy || !online} onClick={save}>{busy ? 'Saving…' : 'Save for offline'}</button>
      {error && <p className="form-error">{error}</p>}
    </section>
  }
  return <section className="offline-card saved">
    <div><strong><CloudOff size={16} /> {destination.name} is saved for offline</strong><p>{info.places} places · {size(info.size)} · saved {new Date(info.savedAt).toLocaleDateString()}</p></div>
    <div className="offline-actions">
      <button type="button" className="chip" disabled={busy || !online} onClick={save}><RefreshCw size={14} /> {busy ? 'Updating…' : 'Update'}</button>
      <button type="button" className="chip" onClick={remove}><Trash2 size={14} /> Remove</button>
    </div>
    {error && <p className="form-error">{error}</p>}
  </section>
}
