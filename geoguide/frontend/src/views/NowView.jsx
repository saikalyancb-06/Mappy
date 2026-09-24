import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { getNow } from '../api'
import RichText from '../components/RichText'
import SearchBox from '../components/SearchBox'
import { AdvisoryList, Chip, IconCircleButton, Notices, PlaceCard, SectionTitle, StateMessage, WeatherPill } from '../components/ui'
import { formatMinutes, localTimeLabel } from '../format'

export default function NowView({ onChooseDestination, context, language, interests, onOpen, onSave, savedIds, userName }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('for_you')

  const latest = useRef(0)
  const load = useCallback(async () => {
    const id = ++latest.current // only the newest request may update the screen
    setLoading(true)
    setError('')
    try {
      const result = await getNow(context, language)
      if (id === latest.current) setData(result)
    } catch (requestError) {
      if (id === latest.current) setError(requestError.message)
    } finally {
      if (id === latest.current) setLoading(false)
    }
  }, [context, language])

  useEffect(() => {
    const timer = window.setTimeout(load, 0) // defer so the effect itself never sets state synchronously
    return () => window.clearTimeout(timer)
  }, [load])

  const place = data?.place
  const suggestions = (data?.suggestions || []).filter((item) => filter === 'for_you' || interests.find((interest) => interest.id === filter)?.categories?.includes(item.category))
  const weather = data?.weather
  const reference = data?.geo_context?.reference
  return <div className="view-content">
    <header className="app-header"><div><span className="eyebrow">Right now</span><h1>Hi{userName ? ` ${userName.split(' ')[0]}` : ''} <span className="wave">👋</span></h1></div><WeatherPill weather={weather} /></header>
    <SearchBox context={context} onPlace={onOpen} onDestination={onChooseDestination} />
    <section className="destination-heading">
      <span className="country-label">{reference?.origin === 'user_location' ? 'Around you' : 'Exploring'}{place?.region ? ` · ${place.region}` : ''}</span>
      <h2>{place?.name || reference?.label || '…'}</h2>
      <p>{localTimeLabel(data?.local_time) || 'Loading live context'}{data?.timezone ? ` · local time` : ''}</p>
    </section>
    {loading && <div className="skeleton-card" />}
    {!loading && error && <StateMessage title="Could not load your briefing" body={error} action={<button className="secondary-button" onClick={load} type="button"><RefreshCw size={16} /> Try again</button>} />}
    {!loading && data && <>
      <section className="briefing-card">
        <div className="verified-row"><span className="verified-dot" /> Briefing <span className="verified-badge">{data.briefing.mode === 'deterministic' ? 'From verified data' : 'Grounded AI summary'}</span></div>
        <RichText text={data.briefing.text} sources={data.briefing.sources} />
        <IconCircleButton label="Refresh" onClick={load}><RefreshCw size={16} /></IconCircleButton>
      </section>
      <div className="context-grid">
        <div><span className="context-icon">☼</span><span>Daylight left</span><strong>{data.daylight_left_min != null ? formatMinutes(data.daylight_left_min) : 'Unknown'}</strong></div>
        <div><span className="context-icon">⌁</span><span>Today</span><strong>{weather?.status === 'ok' && weather.day ? `${Math.round(weather.day.temp_min_c)}–${Math.round(weather.day.temp_max_c)}°C · ${weather.day.precipitation_probability_max ?? '?'}% rain` : 'Weather unavailable'}</strong></div>
      </div>
      <SectionTitle eyebrow="Safety" action={<span className="muted-label">{data.advisories.length ? `${data.advisories.length} active` : 'None recorded'}</span>}>What needs attention</SectionTitle>
      <AdvisoryList advisories={data.advisories} />
      {data.events.length > 0 && <>
        <SectionTitle eyebrow="Happening">Events & festivals</SectionTitle>
        <div className="advisory-list">{data.events.map((event) => <div className="advisory severity-info" key={event.id}><div><strong>{event.title}</strong><span className="advisory-meta">{event.timing === 'dated' ? `${event.start_date} → ${event.end_date || event.start_date}` : 'Usually around this time; dates vary'}</span>{event.summary && <p>{event.summary}</p>}</div></div>)}</div>
      </>}
      <SectionTitle eyebrow="Suggested now">Worth your time</SectionTitle>
      <div className="chip-row">
        <Chip active={filter === 'for_you'} onClick={() => setFilter('for_you')}>For you</Chip>
        {interests.slice(0, 5).map((interest) => <Chip key={interest.id} active={filter === interest.id} onClick={() => setFilter(interest.id)}>{interest.label}</Chip>)}
      </div>
      <div className="place-list">
        {suggestions.length ? suggestions.map((item, index) => <PlaceCard key={item.id} place={item} featured={index === 0} onOpen={onOpen} saved={savedIds.includes(item.id)} onSave={onSave} />) : <StateMessage title="No matching places" body="Try another filter, or explore the Nearby tab." />}
      </div>
      {data.pack && <p className="provenance">Data: {data.pack.provenance.dataset}{data.pack.provenance.version ? ` v${data.pack.provenance.version}` : ''} · weather: {data.pack.provenance.weather}</p>}
      <Notices items={(data.provider_errors || []).map((item) => item.source === 'serpapi' ? null : `${item.source}: ${item.message}`)} />
    </>}
  </div>
}
