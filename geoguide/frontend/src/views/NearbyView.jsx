import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getNearby } from '../api'
import { Chip, IconCircleButton, Notices, PlaceCard, SectionTitle, StateMessage } from '../components/ui'

export default function NearbyView({ context, config, onOpen, onSave, savedIds, onEnableLocation }) {
  const hasDestination = Boolean(context.destination)
  const hasLocation = Boolean(context.userLocation)
  const [origin, setOrigin] = useState(hasDestination ? 'destination' : 'user')
  const [group, setGroup] = useState('all')
  const [openNow, setOpenNow] = useState(false)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const effectiveOrigin = origin === 'destination' && !hasDestination ? 'user' : origin
  const latest = useRef(0)
  const load = useCallback(async () => {
    const id = ++latest.current // only the newest request may update the screen
    if (effectiveOrigin === 'user' && !hasLocation) { setData(null); setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const result = await getNearby(context, { origin: effectiveOrigin, group: group === 'all' ? null : group, openNow })
      if (id === latest.current) setData(result)
    } catch (requestError) {
      if (id === latest.current) setError(requestError.message)
    } finally {
      if (id === latest.current) setLoading(false)
    }
  }, [context, effectiveOrigin, group, openNow, hasLocation])

  useEffect(() => {
    const timer = window.setTimeout(load, 0) // defer so the effect itself never sets state synchronously
    return () => window.clearTimeout(timer)
  }, [load])

  const groups = (config?.groups || []).filter((item) => !['services', 'art', 'cafes', 'nightlife'].includes(item.id))
  const reference = data?.geo_context?.reference
  const notices = (data?.provider_errors || []).map((item) => item.code === 'web_search_unavailable' ? 'Live web search is off; showing stored places only.' : `${item.source}: ${item.message}`)
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">{reference ? (reference.origin === 'user_location' ? 'Around you' : `In ${reference.label}`) : 'Around you'}</span><h1>Nearby</h1></div><IconCircleButton label="Refresh nearby" onClick={load}><RefreshCw size={20} /></IconCircleButton></header>
    <div className="segmented">
      <button className={effectiveOrigin === 'destination' ? 'active' : ''} disabled={!hasDestination} onClick={() => setOrigin('destination')} type="button">{hasDestination ? context.destination.name : 'Destination'}</button>
      <button className={effectiveOrigin === 'user' ? 'active' : ''} onClick={() => setOrigin('user')} type="button">Near me</button>
    </div>
    <div className="chip-row">
      <Chip active={group === 'all'} onClick={() => setGroup('all')}>Things to do</Chip>
      {groups.map((item) => <Chip key={item.id} active={group === item.id} onClick={() => setGroup(item.id)}>{item.label}</Chip>)}
    </div>
    <div className="chip-row"><Chip active={openNow} onClick={() => setOpenNow((value) => !value)}>Open now</Chip></div>
    {effectiveOrigin === 'user' && !hasLocation && <StateMessage title="Location is off" body="“Near me” uses your device location. Turn it on, or switch to your destination." action={<button className="secondary-button" onClick={onEnableLocation} type="button">Use my location</button>} />}
    {loading && <div className="skeleton-card short" />}
    {!loading && error && <StateMessage title="Could not load places" body={error} action={<button className="secondary-button" onClick={load} type="button">Try again</button>} />}
    {!loading && data && <>
      <SectionTitle eyebrow={`${data.items.length} results · within ${reference?.radius_km} km`} action={<span className="muted-label">{reference?.origin === 'user_location' ? 'From you' : `From ${reference?.label} centre`}</span>}>Places worth your time</SectionTitle>
      <div className="place-list">{data.items.length ? data.items.map((place) => <PlaceCard key={place.id} place={place} saved={savedIds.includes(place.id)} onSave={onSave} onOpen={onOpen} />) : <StateMessage title="Nothing verified here yet" body="Try another category or turn off “Open now”." />}</div>
      <Notices items={notices} />
    </>}
  </div>
}
