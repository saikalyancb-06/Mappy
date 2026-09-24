import { useEffect, useState } from 'react'
import { MapPin, Trash2 } from 'lucide-react'
import { getPlace } from '../api'
import { mapsPlaceUrl } from '../share'
import { SectionTitle } from './ui'

// The traveller's saved places (also the plan's must-sees), with quick map and remove actions.
export default function SavedPlaces({ savedIds, onToggleSaved, onOpen }) {
  const [places, setPlaces] = useState({})
  useEffect(() => {
    let cancelled = false
    const missing = savedIds.filter((id) => !places[id])
    if (!missing.length) return undefined
    Promise.all(missing.map((id) => getPlace(id).then((result) => result.place).catch(() => null))).then((found) => {
      if (cancelled) return
      setPlaces((current) => ({ ...current, ...Object.fromEntries(found.filter(Boolean).map((place) => [place.id, place])) }))
    })
    return () => { cancelled = true }
  }, [savedIds]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!savedIds.length) return null
  const shown = savedIds.map((id) => places[id]).filter(Boolean)
  return <>
    <SectionTitle eyebrow="Saved">Your places · {savedIds.length}</SectionTitle>
    <p className="muted-text">Saved places become must-sees when you build a plan.</p>
    <div className="similar-list">{shown.map((place) => <div key={place.id} className="similar-item saved-row">
      <button type="button" className="link-button" onClick={() => onOpen(place)}><strong>{place.name}</strong></button>
      <small>{[place.category, place.address].filter(Boolean).join(' · ')}</small>
      <div className="saved-actions">
        {mapsPlaceUrl(place) && <a className="chip" href={mapsPlaceUrl(place)} target="_blank" rel="noopener noreferrer"><MapPin size={14} /> Map</a>}
        <button type="button" className="chip" onClick={() => onToggleSaved(place)}><Trash2 size={14} /> Remove</button>
      </div>
    </div>)}</div>
  </>
}
