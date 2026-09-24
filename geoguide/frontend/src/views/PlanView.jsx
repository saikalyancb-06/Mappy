import { useState } from 'react'
import { CalendarPlus, CheckCircle2, Clock3, Footprints, Layers, Leaf, Lock, Map as MapIcon, PiggyBank, Share2, Sparkles, Sunset } from 'lucide-react'
import { buildPlan, getPlanDeck } from '../api'
import { downloadFile, mapsRouteUrl, planSummary, planToIcs, shareText } from '../share'
import SearchBox from '../components/SearchBox'
import SwipeDeck from '../components/SwipeDeck'
import FeedbackSheet from '../components/FeedbackSheet'
import { Chip, Notices, SectionTitle, StateMessage, Understood } from '../components/ui'
import { formatDistance, formatMinutes, formatMoney, titleCase } from '../format'

const DURATION_LABELS = { '2h': '2 hours', '4h': '4 hours', full: 'Full day' }
const PRESETS = [
  { id: 'cheaper', label: 'Cheaper', Icon: PiggyBank },
  { id: 'greener', label: 'Greener', Icon: Leaf },
  { id: 'less_walking', label: 'Less walking', Icon: Footprints },
]
const MODE = { walk: 'Walk', bicycle: 'Cycle', auto_rickshaw: 'Auto-rickshaw', motorbike: 'Bike', car: 'Car', transit: 'Bus/metro' }
const EXAMPLES = ['Temples and a sunset spot, no museums, under ₹500', 'Free from 4–8 PM, something peaceful, by bike', 'With kids, avoid crowded places, lots of shade']

export default function PlanView({ context, config, savedIds, onToggleSaved, onOpen, onEnableLocation, profile }) {
  const [duration, setDuration] = useState('4h')
  const [dayOffset, setDayOffset] = useState(0)
  const [wishes, setWishes] = useState('')
  const [travelMode, setTravelMode] = useState(profile?.travel_mode || null)
  const [plan, setPlan] = useState(null)
  const [shareNote, setShareNote] = useState('')
  const [done, setDone] = useState([])
  const [weather, setWeather] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notices, setNotices] = useState([])
  const [deck, setDeck] = useState(null) // { cards, understood, wish_coverage }
  const [decisions, setDecisions] = useState({}) // id → 'like' | 'pass'
  const [history, setHistory] = useState([]) // decided ids, newest last, for undo
  const [deckBusy, setDeckBusy] = useState(false)
  const [feedbackFor, setFeedbackFor] = useState(null) // a stop just marked done: "How was it?"
  const liked = Object.keys(decisions).filter((id) => decisions[id] === 'like')
  const passed = Object.keys(decisions).filter((id) => decisions[id] === 'pass')

  const resetDeck = () => { setDeck(null); setDecisions({}); setHistory([]) }
  const openDeck = async () => {
    setDeckBusy(true)
    setError('')
    try {
      const result = await getPlanDeck({ context, wishes: wishes.trim(), dayOffset, lockedIds: savedIds, excludedIds: passed, profile: travelMode ? { travel_mode: travelMode } : null })
      setDeck(result)
      setNotices((result.provider_errors || []).filter((item) => item.code !== 'web_search_unavailable').map((item) => `${item.source}: ${item.message}`))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setDeckBusy(false)
    }
  }
  const decide = (card, verdict) => { setDecisions((current) => ({ ...current, [card.id]: verdict })); setHistory((current) => [...current, card.id]) }
  const undo = () => {
    const last = history[history.length - 1]
    if (!last) return
    setHistory((current) => current.slice(0, -1))
    setDecisions((current) => { const next = { ...current }; delete next[last]; return next })
  }

  const run = async ({ preset = 'balanced', previous = null, replan = null } = {}) => {
    setBusy(true)
    setError('')
    try {
      const result = await buildPlan({ context, duration, preset, dayOffset, start: dayOffset ? '08:00' : null, lockedIds: [...new Set([...savedIds, ...liked])], excludedIds: passed, previous, wishes: wishes.trim(), replan, profile: travelMode ? { travel_mode: travelMode } : null })
      setPlan(result.plan)
      if (!replan) setDone([])
      setWeather(result.weather)
      setNotices((result.provider_errors || []).map((item) => `${item.source}: ${item.message}`))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  // Re-optimise the rest of the plan from where the traveller actually is.
  const replanFrom = (state) => run({ replan: { previous: plan, completed_ids: [...done, ...(state.completed || [])], ...state } })
  const markDone = (stop) => { setDone((current) => [...current, stop.poi_id]); setFeedbackFor({ id: stop.poi_id, name: stop.name, category: stop.category }); replanFrom({ completed: [stop.poi_id], now: stop.depart }) }

  if (!context.destination && !context.userLocation) {
    return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Shape the day</span><h1>Your plan</h1></div></header><StateMessage title="Where should we plan?" body="Choose a destination or turn on your location to build a plan." action={<button className="secondary-button" onClick={onEnableLocation} type="button">Use my location</button>} /></div>
  }
  const durations = config?.plan_durations || Object.keys(DURATION_LABELS)
  const totals = plan?.totals
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">Shape the day{context.destination ? ` · ${context.destination.name}` : ''}</span><h1>Your plan</h1></div><span className="status-pill">{plan ? (plan.replanned ? 'Re-optimised' : titleCase(plan.preset)) : 'Draft'}</span></header>
    <label className="wishes-box"><span className="eyebrow">What would you like to do?</span>
      <textarea value={wishes} onChange={(event) => { setWishes(event.target.value); resetDeck() }} rows={3} placeholder="Tell GeoGuide in your words — places you want, things to avoid, budget, time…" />
    </label>
    <div className="chip-row">{EXAMPLES.map((text) => <Chip key={text} onClick={() => { setWishes(text); resetDeck() }}>{text}</Chip>)}</div>
    <div className="chip-row">{durations.map((key) => <Chip key={key} active={duration === key} onClick={() => setDuration(key)}>{DURATION_LABELS[key] || key}</Chip>)}</div>
    <div className="chip-row"><Chip active={dayOffset === 0} onClick={() => setDayOffset(0)}>Starting now</Chip><Chip active={dayOffset === 1} onClick={() => setDayOffset(1)}>Tomorrow 8:00</Chip></div>
    <div className="chip-row">{(config?.travel_modes || []).map((mode) => <Chip key={mode.id} active={travelMode === mode.id} onClick={() => setTravelMode((current) => current === mode.id ? null : mode.id)}>{mode.label}</Chip>)}</div>
    <SearchBox context={context} kind="place" placeholder="Add a must-see place…" onPlace={(place) => { if (!savedIds.includes(place.id)) onToggleSaved(place) }} />
    {savedIds.length > 0 && <p className="muted-text"><Lock size={13} /> {savedIds.length} saved place{savedIds.length > 1 ? 's are' : ' is'} kept in the plan when they fit.</p>}
    {profile?.max_daily_budget && <p className="muted-text">Daily budget: {formatMoney(profile.max_daily_budget, profile.budget_currency)} — plans stay within it unless you name another amount.</p>}
    {!deck && <button className="secondary-button full-width" onClick={openDeck} disabled={deckBusy} type="button"><Layers size={17} /> {deckBusy ? 'Finding places…' : 'Swipe to pick places'}</button>}
    {deck && <>
      <Understood items={deck.understood} />
      {deck.wish_coverage?.some((w) => w.status !== 'available') && <Notices items={deck.wish_coverage.filter((w) => w.status !== 'available').map((w) => w.note)} />}
      {deck.cards.length ? <SwipeDeck cards={deck.cards} decisions={decisions} onDecide={decide} onUndo={undo} onOpen={onOpen} /> : <StateMessage title="No places match" body="Nothing stored matches these wishes here. Try other wishes." />}
      <button type="button" className="link-button" onClick={resetDeck}>Close the cards</button>
    </>}
    <button className="primary-button full-width" onClick={() => run()} disabled={busy} type="button"><Sparkles size={17} /> {busy ? 'Planning…' : deck && liked.length ? `Build plan with my ${liked.length} pick${liked.length > 1 ? 's' : ''}` : plan ? 'Rebuild plan' : 'Build my plan'}</button>
    {(liked.length > 0 || passed.length > 0) && <p className="muted-text">{liked.length} to visit (kept in the plan when they fit) · {passed.length} skipped (left out)</p>}
    {error && <StateMessage title="Could not build a plan" body={error} />}
    {plan && <>
      <Understood items={plan.understood} />
      {plan.wish_coverage?.length > 0 && <div className="coverage-row">{plan.wish_coverage.map((item) => <span key={item.key} className={`coverage-chip ${item.status === 'planned' ? 'ok' : 'miss'}`} title={item.note || ''}>{item.status === 'planned' ? '✓' : '✗'} {item.wish}</span>)}</div>}
      {plan.explanation?.length > 0 && <div className="briefing-card">{plan.explanation.map((line) => <p key={line}>{line}</p>)}</div>}
      {plan.completed?.length > 0 && <p className="muted-text"><CheckCircle2 size={13} /> Done: {plan.completed.map((s) => s.name).join(', ')}</p>}
      {plan.stops.length === 0 ? <StateMessage title="Nothing fits this window" body="Try a longer time window, a bigger budget or fewer exclusions." /> : <div className="timeline-card">
        {plan.stops.map((stop, index) => <div className="timeline-stop" key={stop.poi_id}>
          <span className="stop-number">{stop.position}</span>
          <div>
            <span className="eyebrow">{stop.arrive}–{stop.depart}{stop.locked ? ' · must-see' : ''}{stop.confidence ? ` · ${stop.confidence} confidence` : ''}</span>
            <h3><button type="button" className="link-button" onClick={() => onOpen({ id: stop.poi_id, name: stop.name, category: stop.category, lat: stop.lat, lon: stop.lon, reasons: stop.reasons, sources: [] })}>{stop.name}</button></h3>
            <p>{MODE[stop.leg.mode] || stop.leg.mode} {formatMinutes(stop.leg.minutes)} · {formatDistance(stop.leg.distance_km)} from {stop.leg.from}{stop.leg.cost ? ` · ~${formatMoney(String(stop.leg.cost), stop.fee_currency)}` : ''}</p>
            {stop.timing_note && <p className="timing-note"><Sunset size={13} /> {stop.timing_note.charAt(0).toUpperCase() + stop.timing_note.slice(1)}{stop.free_min ? ` · ${formatMinutes(stop.free_min)} free before this` : ''}</p>}
            <p>Visit {formatMinutes(stop.visit_min)}{stop.entry_cost != null ? ` · entry ${formatMoney(stop.entry_cost, stop.fee_currency)}` : ' · entry not verified'}{stop.open_check === 'hours_unknown' ? ' · hours not verified' : ''}{stop.wait_min ? ` · waits ${stop.wait_min} min for opening` : ''}</p>
            {stop.conflicts?.length > 0 && <p className="conflict-note">Sources disagree about this stop — verify before going.</p>}
            {index === 0 && <div className="stop-actions">
              <button type="button" className="chip" disabled={busy} onClick={() => markDone(stop)}><CheckCircle2 size={14} /> Done</button>
              <button type="button" className="chip" disabled={busy} onClick={() => replanFrom({ current_stop_id: stop.poi_id, extra_minutes: 15, now: stop.arrive })}><Clock3 size={14} /> Staying +15 min</button>
              <button type="button" className="chip" disabled={busy} onClick={() => replanFrom({ current_stop_id: stop.poi_id, extra_minutes: 30, now: stop.arrive })}>+30 min</button>
              <button type="button" className="chip" disabled={busy} onClick={() => replanFrom({ skipped_ids: [stop.poi_id] })}>Skip</button>
            </div>}
          </div>
        </div>)}
      </div>}
      {plan.stops.length > 0 && <div className="export-row">
        <button type="button" className="chip" onClick={() => { const ics = planToIcs(plan, context.destination?.name); if (ics) downloadFile(`geoguide-plan-${(plan.start || '').slice(0, 10) || 'day'}.ics`, ics) }}><CalendarPlus size={15} /> Add to calendar</button>
        {mapsRouteUrl(plan.stops, { origin: context.userLocation, mode: profile?.travel_mode }) && <a className="chip" href={mapsRouteUrl(plan.stops, { origin: context.userLocation, mode: profile?.travel_mode })} target="_blank" rel="noopener noreferrer"><MapIcon size={15} /> Route in Maps</a>}
        <button type="button" className="chip" onClick={async () => { const outcome = await shareText({ title: 'My GeoGuide plan', text: planSummary(plan, context.destination?.name), url: mapsRouteUrl(plan.stops, { mode: profile?.travel_mode }) }); setShareNote(outcome === 'copied' ? 'Plan copied — paste it anywhere.' : outcome === 'failed' ? 'Could not share from this browser.' : '') }}><Share2 size={15} /> Share</button>
        {shareNote && <span className="muted-text">{shareNote}</span>}
      </div>}
      {plan.stops.length > 0 && <button type="button" className="secondary-button full-width" disabled={busy} onClick={() => replanFrom({ extra_minutes: 20 })}><Clock3 size={16} /> Running 20 min late — re-plan the rest</button>}
      {totals && <div className="context-grid totals">
        <div><span>Time used</span><strong>{formatMinutes(plan.used_min)} of {formatMinutes(plan.window_min)}</strong></div>
        <div><span>Est. cost</span><strong>{totals.cost_display || '—'}{totals.cost_complete ? '' : ' +'}</strong>{totals.budget_cap && <small className={totals.within_budget ? 'open-yes' : 'open-no'}>{totals.within_budget ? 'within' : 'over'} {formatMoney(totals.budget_cap, totals.cost_currency)}</small>}</div>
        <div><span>Est. CO₂</span><strong>{Math.round(totals.co2_g)} g</strong></div>
        <div><span>Walking</span><strong>{formatDistance(totals.walking_km) || '0 m'}</strong></div>
      </div>}
      <p className="muted-text">{plan.estimates_note}{totals && !totals.cost_complete ? ' Some fees are not verified, so the cost is a lower bound.' : ''}</p>
      {weather?.status === 'ok' && weather.signals?.length > 0 && <p className="muted-text">Planned around {weather.live === false ? 'the dataset weather record' : 'the forecast'}: {weather.signals.join(', ')}.</p>}
      <SectionTitle eyebrow="Re-plan">Choose what matters</SectionTitle>
      <div className="replan-row">{PRESETS.filter((preset) => (config?.plan_presets || []).includes(preset.id)).map(({ id, label, Icon }) => <Chip key={id} active={plan.preset === id} disabled={busy} onClick={() => run({ preset: id, previous: plan })}><Icon size={15} /> {label}</Chip>)}</div>
      <Notices items={[...(plan.warnings || []), ...notices]} />
      {plan.unscheduled?.length > 0 && <details className="unscheduled"><summary>{plan.unscheduled.length} places didn't fit</summary><ul>{plan.unscheduled.map((item) => <li key={item.id}>{item.name} — {item.reason}</li>)}</ul></details>}
    </>}
    {feedbackFor && <FeedbackSheet place={feedbackFor} onClose={() => setFeedbackFor(null)} />}
  </div>
}
