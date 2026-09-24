import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, SlidersHorizontal } from 'lucide-react'
import { getHotels, getNearby } from '../api'
import SearchBox from '../components/SearchBox'
import { Chip, IconCircleButton, Notices, PlaceCard, SectionTitle, StateMessage, Understood } from '../components/ui'

export default function NearbyView({ context, config, onOpen, onSave, savedIds, onEnableLocation, onChooseDestination, hasBudget }) {
  const hasDestination = Boolean(context.destination)
  const hasLocation = Boolean(context.userLocation)
  const [tab, setTab] = useState('places') // places | hotels
  const [origin, setOrigin] = useState(hasDestination ? 'destination' : 'user')
  const [group, setGroup] = useState('all')
  const [openNow, setOpenNow] = useState(false)
  const [rankingMode, setRankingMode] = useState(null)
  const [withinBudget, setWithinBudget] = useState(false)
  const [sort, setSort] = useState('best')
  const [draft, setDraft] = useState('')
  const [text, setText] = useState('')
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
      const result = tab === 'hotels'
        ? await getHotels(context, { origin: effectiveOrigin, sort, withinBudget })
        : await getNearby(context, { origin: effectiveOrigin, group: group === 'all' ? null : group, openNow, text, rankingMode, withinBudget })
      if (id === latest.current) setData(result)
    } catch (requestError) {
      if (id === latest.current) setError(requestError.message)
    } finally {
      if (id === latest.current) setLoading(false)
    }
  }, [context, effectiveOrigin, group, openNow, hasLocation, tab, sort, text, rankingMode, withinBudget])

  useEffect(() => {
    const timer = window.setTimeout(load, 0) // defer so the effect itself never sets state synchronously
    return () => window.clearTimeout(timer)
  }, [load])

  const groups = (config?.groups || []).filter((item) => !['services', 'art', 'cafes', 'nightlife', 'stay'].includes(item.id))
  const reference = data?.geo_context?.reference
  const notices = (data?.provider_errors || []).map((item) => item.code === 'web_search_unavailable' ? (tab === 'hotels' ? 'Live hotel prices are off (web search not configured); showing stored hotels without rates.' : 'Live web search is off; showing stored places only.') : `${item.source}: ${item.message}`)
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">{reference ? (reference.origin === 'user_location' ? 'Around you' : `In ${reference.label}`) : 'Around you'}</span><h1>{tab === 'hotels' ? 'Stays' : 'Nearby'}</h1></div><IconCircleButton label="Refresh" onClick={load}><RefreshCw size={20} /></IconCircleButton></header>
    <SearchBox context={context} placeholder={tab === 'hotels' ? 'Search a hotel you heard about…' : 'Search a place, hotel or area…'} kind={tab === 'hotels' ? 'stay' : null} onPlace={onOpen} onDestination={onChooseDestination} />
    <div className="segmented three">
      <button className={tab === 'places' ? 'active' : ''} onClick={() => setTab('places')} type="button">Places</button>
      <button className={tab === 'hotels' ? 'active' : ''} onClick={() => setTab('hotels')} type="button">Hotels</button>
    </div>
    <div className="segmented">
      <button className={effectiveOrigin === 'destination' ? 'active' : ''} disabled={!hasDestination} onClick={() => setOrigin('destination')} type="button">{hasDestination ? context.destination.name : 'Destination'}</button>
      <button className={effectiveOrigin === 'user' ? 'active' : ''} onClick={() => setOrigin('user')} type="button">Near me</button>
    </div>
    {tab === 'places' ? <>
      <form className="constraint-form" onSubmit={(event) => { event.preventDefault(); setText(draft.trim()) }}>
        <SlidersHorizontal size={16} />
        <input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="e.g. peaceful, under ₹500, open now, 20 min by bike" aria-label="Describe what you want" />
        <button type="submit" className="chip active">Apply</button>
        {text && <button type="button" className="chip" onClick={() => { setDraft(''); setText('') }}>Clear</button>}
      </form>
      <Understood items={data?.understood} />
      <div className="chip-row">
        <Chip active={group === 'all'} onClick={() => setGroup('all')}>Things to do</Chip>
        {groups.map((item) => <Chip key={item.id} active={group === item.id} onClick={() => setGroup(item.id)}>{item.label}</Chip>)}
      </div>
      <div className="chip-row">
        <Chip active={openNow} onClick={() => setOpenNow((value) => !value)}>Open now</Chip>
        {(config?.ranking_modes || []).map((mode) => <Chip key={mode.id} active={rankingMode === mode.id} onClick={() => setRankingMode((current) => current === mode.id ? null : mode.id)}>{mode.label}</Chip>)}
        {hasBudget && <Chip active={withinBudget} onClick={() => setWithinBudget((value) => !value)}>Within my budget</Chip>}
      </div>
    </> : <div className="chip-row">
      {(config?.hotel_sorts || []).map((item) => <Chip key={item.id} active={sort === item.id} onClick={() => setSort(item.id)}>{item.label}</Chip>)}
      {hasBudget && <Chip active={withinBudget} onClick={() => setWithinBudget((value) => !value)}>Within my budget</Chip>}
    </div>}
    {effectiveOrigin === 'user' && !hasLocation && <StateMessage title="Location is off" body="“Near me” uses your device location. Turn it on, or switch to your destination." action={<button className="secondary-button" onClick={onEnableLocation} type="button">Use my location</button>} />}
    {loading && <div className="skeleton-card short" />}
    {!loading && error && <StateMessage title="Could not load" body={error} action={<button className="secondary-button" onClick={load} type="button">Try again</button>} />}
    {!loading && data && <>
      <SectionTitle eyebrow={`${data.items.length} results${reference ? ` · within ${reference.radius_km} km` : ''}`} action={<span className="muted-label">{reference?.origin === 'user_location' ? 'From you' : `From ${reference?.label} centre`}</span>}>{tab === 'hotels' ? `Best stays${data.check_in ? ` · ${data.check_in}` : ''}` : 'Places worth your time'}</SectionTitle>
      <div className="place-list">{data.items.length ? data.items.map((place) => (!place.name.toLowerCase().includes('masjid') && !place.name.toLowerCase().includes('girls')) && <PlaceCard key={place.id} place={place} saved={savedIds.includes(place.id)} onSave={tab === 'hotels' ? null : onSave} onOpen={onOpen} />) : <StateMessage title="Nothing matches" body={tab === 'hotels' ? 'No stays match these filters here.' : 'Try loosening the filters or another category.'} />}</div>
      {tab === 'hotels' && data.items.length > 0 && !data.prices_available && <p className="muted-text">Nightly rates appear only when a live source publishes them; none are invented.</p>}
      <Notices items={notices} />
    </>}
  </div>
}
