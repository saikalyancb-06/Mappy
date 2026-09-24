import { useCallback, useEffect, useRef, useState } from 'react'
import { BedDouble, CalendarDays, ChevronLeft, ChevronRight, Compass, MessageCircle, RefreshCw, UtensilsCrossed, Volume2 } from 'lucide-react'
import { getCityContext, getEventsOverview, getHotels, getNearby } from '../api'
import RichText from '../components/RichText'
import SearchBox from '../components/SearchBox'
import { AdvisoryList, Chip, EventCard, IconCircleButton, Notices, PlaceCard, SectionTitle, StateMessage, TipList } from '../components/ui'
import { addDays, formatDay, relativeDay, titleCase, upcomingSaturday } from '../format'

// City = where, Date = when, mode = what. Changing the date re-runs the whole context on the server.
const MODES = [
  ['overview', 'Overview'], ['happening', "What's happening"], ['todo', 'Things to do'], ['food', 'Food'],
  ['hotels', 'Hotels'], ['history', 'History'], ['culture', 'Culture'], ['ask', 'Ask GeoGuide'],
]
const SPEECH_LANG = { en: 'en-IN', kn: 'kn-IN', hi: 'hi-IN' }

const firstSentences = (text, count = 2) => (text || '').split(/(?<=[.!?])\s+/).slice(0, count).join(' ')

function useLatest(loader) {
  // Only the newest request may update the screen.
  const [state, setState] = useState({ data: null, loading: false, error: '' })
  const latest = useRef(0)
  const run = useCallback(async () => {
    const id = ++latest.current
    setState((current) => ({ ...current, loading: true, error: '' }))
    try {
      const data = await loader()
      if (id === latest.current) setState({ data, loading: false, error: '' })
    } catch (error) {
      if (id === latest.current) setState({ data: null, loading: false, error: error.message })
    }
  }, [loader])
  return [state, run]
}

function WeatherCard({ weather, dateLabel }) {
  if (!weather || weather.status !== 'ok' || !weather.day) {
    return <div className="weather-card unavailable"><span className="eyebrow">Weather · {dateLabel}</span><p>Weather for this date is unavailable{weather?.error?.message ? ` (${weather.error.message})` : ''}. Tips below leave weather out.</p></div>
  }
  const day = weather.day
  const rain = day.precipitation_probability_max != null ? `${day.precipitation_probability_max}% chance of rain` : day.precipitation_mm != null ? `${day.precipitation_mm} mm rain` : null
  return <div className="weather-card">
    <span className="eyebrow">Weather · {dateLabel}</span>
    <div className="weather-main"><strong>{day.temp_min_c != null ? `${Math.round(day.temp_min_c)}–${Math.round(day.temp_max_c)}°C` : '—'}</strong><span>{day.summary}</span></div>
    <p>{[rain, day.apparent_max_c != null ? `feels like up to ${Math.round(day.apparent_max_c)}°C` : null, day.uv_index_max != null ? `UV ${Math.round(day.uv_index_max)}` : null].filter(Boolean).join(' · ')}</p>
    <span className={`basis-pill basis-${weather.basis}`}>{weather.basis_label}</span>
  </div>
}

function DateBar({ selected, today, onChange }) {
  if (!selected) return null
  return <div className="date-bar-wrap">
    <div className="date-bar">
      <button type="button" aria-label="Previous day" onClick={() => onChange(addDays(selected, -1))}><ChevronLeft size={18} /></button>
      <label className="date-field"><CalendarDays size={16} /><input type="date" aria-label="Date" value={selected} onChange={(event) => event.target.value && onChange(event.target.value)} /></label>
      <button type="button" aria-label="Next day" onClick={() => onChange(addDays(selected, 1))}><ChevronRight size={18} /></button>
    </div>
    <div className="chip-row">
      <Chip active={selected === today} onClick={() => onChange(null)}>Today</Chip>
      <Chip active={selected === addDays(today, 1)} onClick={() => onChange(addDays(today, 1))}>Tomorrow</Chip>
      <Chip active={selected === upcomingSaturday(today) && selected !== today} onClick={() => onChange(upcomingSaturday(today))}>This weekend</Chip>
    </div>
  </div>
}

export default function ExploreView({ context, language, selectedDate, onDateChange, onOpen, onSave, savedIds, onAsk, onGoNearby, onChooseDestination }) {
  const [mode, setMode] = useState('overview')
  const [eventsNear, setEventsNear] = useState('destination') // destination | me
  const [eventTab, setEventTab] = useState(null)
  const [ctx, loadContext] = useLatest(useCallback(() => getCityContext(context, { date: selectedDate, language }), [context, selectedDate, language]))
  const selected = ctx.data?.date?.selected || selectedDate
  const origin = context.destination ? 'destination' : 'auto'
  const [events, loadEvents] = useLatest(useCallback(() => getEventsOverview(context, { date: selected, near: eventsNear === 'me' ? 'me' : null }), [context, selected, eventsNear]))
  const [food, loadFood] = useLatest(useCallback(() => getNearby(context, { origin, group: 'food' }), [context, origin]))
  const [hotels, loadHotels] = useLatest(useCallback(() => getHotels(context, { origin }), [context, origin]))

  useEffect(() => { const t = window.setTimeout(loadContext, 0); return () => window.clearTimeout(t) }, [loadContext])
  useEffect(() => {
    if (mode !== 'happening' || !selected) return undefined
    const t = window.setTimeout(loadEvents, 0)
    return () => window.clearTimeout(t)
  }, [mode, selected, loadEvents])
  useEffect(() => {
    const loader = mode === 'food' ? loadFood : mode === 'hotels' ? loadHotels : null
    if (!loader) return undefined
    const t = window.setTimeout(loader, 0)
    return () => window.clearTimeout(t)
  }, [mode, loadFood, loadHotels])

  const data = ctx.data
  const city = data?.city
  const dateLabel = formatDay(selected) || ''
  const speak = () => {
    if (!('speechSynthesis' in window) || !data?.briefing?.text) return
    const utterance = new SpeechSynthesisUtterance(data.briefing.text.replace(/\[E\d+\]/g, '').replaceAll('**', ''))
    utterance.lang = SPEECH_LANG[language] || 'en-IN'
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  }
  const chooseMode = (id) => (id === 'ask' ? onAsk() : setMode(id))
  const eventList = data?.events

  return <div className="view-content explore-view">
    <header className="explore-header">
      <div>
        <span className="country-label">📍 {city ? [city.name, city.country].filter(Boolean).join(', ') : 'Finding your city…'}</span>
        <h1>{city ? `Explore ${city.name}` : 'Explore'}</h1>
        {data && <p className="muted-text">{dateLabel} · {relativeDay(data.date.days_from_today)}{data.season?.name ? ` · ${titleCase(data.season.name)} season` : ''}{data.season?.peak_tourist_season ? ' · peak tourist season' : ''}</p>}
      </div>
      <IconCircleButton label="Refresh" onClick={loadContext}><RefreshCw size={18} /></IconCircleButton>
    </header>
    <DateBar selected={selected} today={data?.date?.today} onChange={onDateChange} />
    <SearchBox context={context} onPlace={onOpen} onDestination={onChooseDestination} />
    <div className="chip-row mode-row" role="tablist" aria-label="Explore sections">{MODES.map(([id, label]) => <Chip key={id} active={mode === id} onClick={() => chooseMode(id)}>{label}</Chip>)}</div>

    {ctx.loading && !data && <div className="skeleton-card" />}
    {ctx.error && <StateMessage title="Could not load this city" body={ctx.error} action={<button className="secondary-button" onClick={loadContext} type="button"><RefreshCw size={16} /> Try again</button>} />}

    {data && mode === 'overview' && <>
      <section className={`briefing-card ${ctx.loading ? 'is-stale' : ''}`}>
        <div className="verified-row"><span className="verified-dot" /> Briefing for {dateLabel}<span className="verified-badge">{data.briefing.mode === 'deterministic' ? 'From verified data' : 'Grounded AI summary'}</span></div>
        <RichText text={data.briefing.text} sources={data.briefing.sources} />
        <button type="button" className="secondary-button listen-button" onClick={speak}><Volume2 size={16} /> Listen</button>
      </section>

      <SectionTitle eyebrow="🎉 Happening" action={<button type="button" className="link-button" onClick={() => setMode('happening')}>See all</button>}>{data.date.kind === 'today' ? 'Happening today' : `Happening on ${formatDay(selected, false)}`}</SectionTitle>
      {eventList.events.length
        ? <div className="event-list">{eventList.events.slice(0, 3).map((event) => <EventCard key={event.id} event={event} />)}</div>
        : <div className="empty-events"><strong>No verified events</strong><p>{eventList.message}</p></div>}
      {eventList.associated_festivals?.length > 0 && <p className="muted-text">Usually around this time (dates not confirmed): {eventList.associated_festivals.map((f) => f.name).join(', ')}.</p>}

      <WeatherCard weather={data.weather} dateLabel={dateLabel} />

      <SectionTitle eyebrow="🧳 Local tips">For {formatDay(selected, false)}</SectionTitle>
      <TipList tips={data.tips} />

      {data.about.length > 0 && <>
        <SectionTitle eyebrow="About this place" action={<button type="button" className="link-button" onClick={() => setMode('history')}>Read more</button>}>History & significance</SectionTitle>
        <div className="knowledge-card"><p>{firstSentences(data.about[0].content, 3)}</p><small>Source: {data.about[0].source}</small></div>
      </>}

      <SectionTitle eyebrow="🏛 Top attractions" action={<button type="button" className="link-button" onClick={() => setMode('todo')}>See all</button>}>Worth your time</SectionTitle>
      <div className="place-list">{data.attractions.length ? data.attractions.slice(0, 3).map((place, index) => <PlaceCard key={place.id} place={place} featured={index === 0} onOpen={onOpen} onSave={onSave} saved={savedIds.includes(place.id)} />) : <StateMessage title="No stored attractions" body="Live search found nothing verified for this city yet." />}</div>

      {data.advisories.length > 0 && <><SectionTitle eyebrow="Safety">Active on this date</SectionTitle><AdvisoryList advisories={data.advisories} /></>}

      <SectionTitle eyebrow="📍 Nearby">Around {city.name}</SectionTitle>
      <div className="shortcut-row">
        <button type="button" onClick={onGoNearby}><Compass size={18} /> Places</button>
        <button type="button" onClick={() => setMode('hotels')}><BedDouble size={18} /> Hotels</button>
        <button type="button" onClick={() => setMode('food')}><UtensilsCrossed size={18} /> Food</button>
        <button type="button" onClick={onAsk}><MessageCircle size={18} /> Ask</button>
      </div>
      <Notices items={(data.provider_errors || []).filter((e) => e.code !== 'web_search_unavailable').map((e) => `${e.source}: ${e.message}`)} />
    </>}

    {data && mode === 'happening' && <>
      <div className="segmented">
        <button type="button" className={eventsNear === 'destination' ? 'active' : ''} onClick={() => setEventsNear('destination')}>In {city.name}</button>
        <button type="button" className={eventsNear === 'me' ? 'active' : ''} disabled={!context.userLocation} onClick={() => setEventsNear('me')}>Near me</button>
      </div>
      {events.loading && <div className="skeleton-card short" />}
      {events.error && <StateMessage title="Could not load events" body={events.error} />}
      {events.data && !events.loading && (() => {
        const tabs = events.data.tabs || []
        const active = tabs.find((tab) => tab.id === eventTab) || tabs[0]
        const shown = active ? events.data.events.filter((event) => active.event_ids.includes(event.id)) : []
        return <>
          <p className="muted-text">{events.data.city.name} · next {events.data.range.label} · {events.data.area.mode === 'near_me' ? `within ${events.data.area.radius_km} km of you` : `within ${events.data.area.radius_km} km of the centre`}</p>
          {tabs.length > 0 && <div className="chip-row" role="tablist" aria-label="Event filters">{tabs.map((tab) => <Chip key={tab.id} active={active?.id === tab.id} onClick={() => setEventTab(tab.id)}>{tab.label} · {tab.count}</Chip>)}</div>}
          {!tabs.length && <div className="empty-events"><strong>No verified events</strong><p>{events.data.message}</p></div>}
          <div className="event-list">{shown.map((event) => <EventCard key={event.id} event={event} />)}</div>
          {events.data.associated_festivals.length > 0 && <>
            <SectionTitle eyebrow="Associated with this city">Dates not confirmed for this period</SectionTitle>
            <div className="event-list">{events.data.associated_festivals.map((event) => <EventCard key={event.id} event={event} />)}</div>
          </>}
          <details className="sources-checked"><summary>Sources checked</summary><ul>{events.data.sources_checked.map((s) => <li key={s.provider}>{s.source}: {s.status === 'ok' ? `checked (${s.kept} of ${s.found} kept${s.cached ? ', cached' : ''})` : s.status === 'not_configured' ? 'not configured on this server' : s.status === 'not_applicable' ? s.error?.message : `unavailable${s.error?.message ? ` — ${s.error.message}` : ''}`}</li>)}</ul></details>
        </>
      })()}
    </>}

    {data && mode === 'todo' && <div className="place-list">{data.attractions.map((place, index) => <PlaceCard key={place.id} place={place} featured={index === 0} onOpen={onOpen} onSave={onSave} saved={savedIds.includes(place.id)} />)}</div>}

    {mode === 'food' && <>
      {food.loading && <div className="skeleton-card short" />}
      {food.error && <StateMessage title="Could not load food places" body={food.error} />}
      {food.data && <div className="place-list">{food.data.items.length ? food.data.items.map((place) => <PlaceCard key={place.id} place={place} onOpen={onOpen} onSave={onSave} saved={savedIds.includes(place.id)} />) : <StateMessage title="No verified food places" body="Nothing verified nearby yet." />}</div>}
    </>}

    {mode === 'hotels' && <>
      {hotels.loading && <div className="skeleton-card short" />}
      {hotels.error && <StateMessage title="Could not load hotels" body={hotels.error} />}
      {hotels.data && <div className="place-list">{hotels.data.items.length ? hotels.data.items.map((place) => <PlaceCard key={place.id} place={place} onOpen={onOpen} />) : <StateMessage title="No stays found" body="No verified stays for this city." />}</div>}
      {hotels.data && !hotels.data.prices_available && <p className="muted-text">Nightly rates appear only when a live source publishes them; none are invented.</p>}
    </>}

    {data && (mode === 'history' || mode === 'culture') && <>
      {(mode === 'history' ? data.about : data.culture).length === 0 && <StateMessage title="No stored background" body={`GeoGuide has no curated ${mode} notes for ${city.name} yet. Ask GeoGuide to search the web.`} />}
      {(mode === 'history' ? data.about : data.culture).map((hit) => <article key={hit.chunk_id} className="knowledge-card"><span className="category-label">{titleCase(hit.category)}</span><h3>{hit.title}</h3><p>{hit.content}</p><small>Source: {hit.source}</small></article>)}
      {mode === 'culture' && <><SectionTitle eyebrow="For this date">Local tips</SectionTitle><TipList tips={data.tips} /></>}
    </>}
  </div>
}
