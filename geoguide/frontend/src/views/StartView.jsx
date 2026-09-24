import { useEffect, useState } from 'react'
import { ChevronRight, LocateFixed, MapPinned, Navigation, Search } from 'lucide-react'
import { listDestinations, resolveDestination } from '../api'
import { formatDistance } from '../format'

// Choose how to start: device location and/or a destination to explore. Both are optional,
// independent choices; nothing is assumed when neither is given.
export default function StartView({ device, here, locating, onUseLocation, onChooseDestination, onContinue, canContinue, onCancel }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState({ query: null, items: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const point = device.location ? { lat: device.location.lat, lon: device.location.lon } : null
  const pointKey = point ? `${point.lat.toFixed(2)},${point.lon.toFixed(2)}` : ''

  // Live list: matches as you type; with location on and nothing typed, the closest cities first.
  useEffect(() => {
    const text = query.trim()
    let cancelled = false
    const timer = window.setTimeout(() => {
      const near = pointKey ? { lat: Number(pointKey.split(',')[0]), lon: Number(pointKey.split(',')[1]) } : null
      listDestinations(text, near, text ? 8 : 6).then((result) => { if (!cancelled) setResults({ query: text, items: result.items || [] }) }).catch(() => { if (!cancelled) setResults({ query: text, items: [] }) })
    }, text ? 200 : 0)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [query, pointKey])

  const search = async (event) => {
    event.preventDefault()
    const text = query.trim()
    if (!text) return
    if (results.query === text && results.items.length) { choose(results.items[0]); return }
    setBusy(true)
    setError('')
    try {
      const { place } = await resolveDestination(text)
      onChooseDestination(place)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }
  const choose = (item) => onChooseDestination({ ...item, destination_id: item.id, kind: 'destination' })

  const typed = query.trim()
  const items = results.items
  let locationLabel = 'Use my current location'
  if (locating || device.status === 'locating') locationLabel = device.location ? 'Finding your city…' : 'Finding your location…'
  else if (device.location) locationLabel = here?.name ? `Explore where I am · ${here.name}` : 'Explore around my location'
  const listTitle = typed ? (items.length ? 'Matching destinations' : null) : point ? 'Closest to you' : 'Popular destinations'

  return <div className="permission-screen">
    <div className="permission-icon"><MapPinned size={34} /></div>
    <span className="eyebrow">Where are you exploring?</span>
    <h1>Start with a place</h1>
    <p>Pick a destination to explore, use your current location, or both. GeoGuide keeps them separate: “near me” always means where you are.</p>

    <button className={`location-cta ${device.location ? 'on' : ''}`} onClick={onUseLocation} disabled={locating || device.status === 'unsupported'} type="button">
      {device.location ? <Navigation size={18} /> : <LocateFixed size={18} />}
      <span><strong>{locationLabel}</strong><small>{device.location ? `Location on · ±${device.location.accuracy_m || '?'} m` : device.status === 'denied' ? 'Location is blocked in your browser settings' : 'GeoGuide asks your browser for permission'}</small></span>
      <ChevronRight size={18} />
    </button>
    {device.error && <p className="form-error">{device.error}</p>}

    <form className="destination-form" onSubmit={search}>
      <label>Search any place<input value={query} onChange={(event) => { setQuery(event.target.value); setError('') }} placeholder="City, state, landmark or area" autoComplete="off" /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </form>
    {listTitle && <span className="eyebrow destination-list-title">{listTitle}</span>}
    {items.length > 0 && <div className="destination-options">{items.map((item) => <button type="button" key={item.id} className={`destination-option ${here?.destination_id === item.id ? 'is-here' : ''}`} onClick={() => choose(item)}>
      <strong>{item.name}{here?.destination_id === item.id ? ' · you are here' : ''}</strong>
      <span>{[item.region, item.country].filter(Boolean).join(', ')}{item.distance_km != null ? ` · ${formatDistance(item.distance_km)} away` : ''}{item.curated ? ` · ${item.poi_count} verified places` : ''}</span>
    </button>)}</div>}
    {typed && results.query === typed && !items.length && <p className="muted-text">No stored destination matches “{typed}”. Search the map for it instead.</p>}
    {typed && <button className="primary-button" disabled={busy} onClick={search} type="button"><Search size={17} /> {busy ? 'Looking it up…' : `Find “${typed}” on the map`}</button>}

    {canContinue && !typed && <button className="secondary-button" onClick={onContinue} type="button">Continue <ChevronRight size={18} /></button>}
    {onCancel && <button className="skip-button" onClick={onCancel} type="button">Back</button>}
  </div>
}
