import { useEffect, useState } from 'react'
import { ChevronRight, LocateFixed, MapPinned, Search } from 'lucide-react'
import { listDestinations, resolveDestination } from '../api'

// Choose how to start: device location and/or a destination to explore. Both are optional,
// independent choices; nothing is assumed when neither is given.
export default function StartView({ device, onLocate, onChooseDestination, onContinue, canContinue, onCancel }) {
  const [query, setQuery] = useState('')
  const [curated, setCurated] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    listDestinations().then((result) => setCurated(result.items || [])).catch(() => setCurated([]))
  }, [])

  const search = async (event) => {
    event.preventDefault()
    if (!query.trim()) return
    setBusy(true)
    setError('')
    try {
      const { place } = await resolveDestination(query.trim())
      onChooseDestination(place)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  const matches = query.trim() ? curated.filter((item) => item.name.toLowerCase().includes(query.trim().toLowerCase())) : curated
  return <div className="permission-screen">
    <div className="permission-icon"><MapPinned size={34} /></div>
    <span className="eyebrow">Where are you exploring?</span>
    <h1>Start with a place</h1>
    <p>Pick a destination to explore, use your current location, or both. GeoGuide keeps them separate: “near me” always means where you are.</p>
    {matches.length > 0 && <div className="destination-options">{matches.map((item) => <button type="button" key={item.id} className="destination-option" onClick={() => onChooseDestination({ ...item, destination_id: item.id, kind: 'destination' })}>
      <strong>{item.name}</strong><span>{[item.region, item.country].filter(Boolean).join(', ')}{item.curated ? ` · ${item.poi_count} verified places` : ''}</span>
    </button>)}</div>}
    <form className="destination-form" onSubmit={search}>
      <label>Search any place<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Town, landmark or area" /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary-button" disabled={!query.trim() || busy} type="submit"><Search size={17} /> {busy ? 'Looking it up…' : 'Find place'}</button>
    </form>
    <button className="secondary-button" onClick={onLocate} disabled={device.status === 'locating' || device.status === 'unsupported'} type="button">
      <LocateFixed size={18} /> {device.location ? 'Location on' : device.status === 'locating' ? 'Finding your location…' : 'Use my current location'}
    </button>
    {device.error && <p className="form-error">{device.error}</p>}
    {canContinue && <button className="primary-button" onClick={onContinue} type="button">Continue <ChevronRight size={18} /></button>}
    {onCancel && <button className="skip-button" onClick={onCancel} type="button">Back</button>}
  </div>
}
