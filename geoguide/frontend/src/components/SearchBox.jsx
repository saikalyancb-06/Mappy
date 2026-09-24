import { useEffect, useRef, useState } from 'react'
import { MapPinned, Search, X } from 'lucide-react'
import { searchPlaces } from '../api'
import { formatDistance, titleCase } from '../format'
import VoiceInputButton from './VoiceInputButton'

// Search a place, hotel, destination or category the traveller has heard of.
// Stored data first; the backend adds live search when stored matches are weak.
export default function SearchBox({ context, placeholder = 'Search a place, hotel or area…', kind = null, onPlace, onDestination }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const latest = useRef(0)

  useEffect(() => {
    const text = query.trim()
    if (text.length < 2) return undefined
    const id = ++latest.current
    const timer = window.setTimeout(async () => {
      setBusy(true)
      setError('')
      try {
        const data = await searchPlaces(context, text, kind)
        if (id === latest.current) setResult(data)
      } catch (requestError) {
        if (id === latest.current) setError(requestError.message)
      } finally {
        if (id === latest.current) setBusy(false)
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [query, context, kind])

  const clear = () => { setQuery(''); setResult(null); setError('') }
  const open = query.trim().length >= 2 && (result || busy || error)
  return <div className="search-box">
    <div className="search-input-row">
      <label className="search-input">
        <Search size={17} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={placeholder} aria-label="Search places" />
        {query && <button type="button" aria-label="Clear search" onClick={clear}><X size={15} /></button>}
      </label>
      <VoiceInputButton
        compact
        onTranscript={(recognizedText) => setQuery(recognizedText)}
        buttonLabel="Voice search"
      />
    </div>
    {open && <div className="search-results" role="listbox">
      {busy && <p className="muted-text">Searching…</p>}
      {error && <p className="form-error">{error}</p>}
      {!busy && result && <>
        {result.category && <p className="muted-text">Showing {titleCase(result.category)} results nearby</p>}
        {(result.destinations || []).map((item) => <button type="button" className="search-result" key={item.id} onClick={() => { onDestination?.({ ...item, destination_id: item.id, kind: 'destination' }); clear() }}><MapPinned size={16} /><span><strong>{item.name}</strong><small>Destination · {[item.region, item.country].filter(Boolean).join(', ')}</small></span></button>)}
        {(result.places || []).map((place) => <button type="button" className="search-result" key={place.id} onClick={() => { onPlace?.(place); clear() }}>
          <span className="search-kind">{place.kind === 'stay' ? '🛏' : '📍'}</span>
          <span><strong>{place.name}</strong><small>{[titleCase(place.category), place.destination_name, place.distance_from_user_km != null ? `${formatDistance(place.distance_from_user_km)} from you` : null, place.cost_for_user?.display].filter(Boolean).join(' · ')}</small></span>
        </button>)}
        {!result.places?.length && !result.destinations?.length && <p className="muted-text">No match for “{query}”.{(result.provider_errors || []).some((e) => e.code === 'web_search_unavailable') ? ' Live search is off, so only stored places were checked.' : ''}</p>}
      </>}
    </div>}
  </div>
}
