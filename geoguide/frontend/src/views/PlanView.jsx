import { useState } from 'react'
import { Leaf, Lock, PiggyBank, Footprints, Sparkles } from 'lucide-react'
import { buildPlan } from '../api'
import { Chip, Notices, SectionTitle, StateMessage } from '../components/ui'
import { formatDistance, formatMinutes, formatMoney, titleCase } from '../format'

const DURATION_LABELS = { '2h': '2 hours', '4h': '4 hours', full: 'Full day' }
const PRESETS = [
  { id: 'cheaper', label: 'Cheaper', Icon: PiggyBank },
  { id: 'greener', label: 'Greener', Icon: Leaf },
  { id: 'less_walking', label: 'Less walking', Icon: Footprints },
]
const MODE = { walk: 'Walk', bicycle: 'Cycle', auto_rickshaw: 'Auto-rickshaw' }

export default function PlanView({ context, config, savedIds, onOpen, onEnableLocation }) {
  const [duration, setDuration] = useState('4h')
  const [dayOffset, setDayOffset] = useState(0)
  const [plan, setPlan] = useState(null)
  const [weather, setWeather] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notices, setNotices] = useState([])

  const run = async (preset = 'balanced', previous = null) => {
    setBusy(true)
    setError('')
    try {
      const result = await buildPlan({ context, duration, preset, dayOffset, start: dayOffset ? '08:00' : null, lockedIds: savedIds, previous })
      setPlan(result.plan)
      setWeather(result.weather)
      setNotices((result.provider_errors || []).map((item) => `${item.source}: ${item.message}`))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setBusy(false)
    }
  }

  if (!context.destination && !context.userLocation) {
    return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Shape the day</span><h1>Your plan</h1></div></header><StateMessage title="Where should we plan?" body="Choose a destination or turn on your location to build a plan." action={<button className="secondary-button" onClick={onEnableLocation} type="button">Use my location</button>} /></div>
  }
  const durations = config?.plan_durations || Object.keys(DURATION_LABELS)
  const totals = plan?.totals
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">Shape the day{context.destination ? ` · ${context.destination.name}` : ''}</span><h1>Your plan</h1></div><span className="status-pill">{plan ? titleCase(plan.preset) : 'Draft'}</span></header>
    <div className="chip-row">{durations.map((key) => <Chip key={key} active={duration === key} onClick={() => setDuration(key)}>{DURATION_LABELS[key] || key}</Chip>)}</div>
    <div className="chip-row"><Chip active={dayOffset === 0} onClick={() => setDayOffset(0)}>Starting now</Chip><Chip active={dayOffset === 1} onClick={() => setDayOffset(1)}>Tomorrow 8:00</Chip></div>
    {savedIds.length > 0 && <p className="muted-text"><Lock size={13} /> {savedIds.length} saved place{savedIds.length > 1 ? 's are' : ' is'} kept in the plan when they fit.</p>}
    <button className="primary-button full-width" onClick={() => run('balanced')} disabled={busy} type="button"><Sparkles size={17} /> {busy ? 'Planning…' : plan ? 'Rebuild plan' : 'Build my plan'}</button>
    {error && <StateMessage title="Could not build a plan" body={error} />}
    {plan && <>
      {plan.explanation?.length > 0 && <div className="briefing-card">{plan.explanation.map((line) => <p key={line}>{line}</p>)}</div>}
      {plan.stops.length === 0 ? <StateMessage title="Nothing fits this window" body="Try a longer time window or another day." /> : <div className="timeline-card">
        {plan.stops.map((stop) => <div className="timeline-stop" key={stop.poi_id}>
          <span className="stop-number">{stop.position}</span>
          <div>
            <span className="eyebrow">{stop.arrive}–{stop.depart}{stop.locked ? ' · saved' : ''}</span>
            <h3><button type="button" className="link-button" onClick={() => onOpen({ id: stop.poi_id, name: stop.name, category: stop.category, lat: stop.lat, lon: stop.lon, reasons: stop.reasons, sources: [] })}>{stop.name}</button></h3>
            <p>{MODE[stop.leg.mode] || stop.leg.mode} {formatMinutes(stop.leg.minutes)} · {formatDistance(stop.leg.distance_km)} from {stop.leg.from}{stop.leg.cost ? ` · ~${formatMoney(stop.leg.cost, stop.fee_currency)}` : ''}</p>
            <p>Visit {formatMinutes(stop.visit_min)}{stop.entry_fee != null ? ` · entry ${formatMoney(stop.entry_fee, stop.fee_currency)}` : ' · entry not verified'}{stop.open_check === 'hours_unknown' ? ' · hours not verified' : ''}{stop.wait_min ? ` · waits ${stop.wait_min} min for opening` : ''}</p>
          </div>
        </div>)}
      </div>}
      {totals && <div className="context-grid totals">
        <div><span>Time used</span><strong>{formatMinutes(plan.used_min)} of {formatMinutes(plan.window_min)}</strong></div>
        <div><span>Est. cost</span><strong>{formatMoney(totals.cost, totals.cost_currency) || '—'}{totals.cost_complete ? '' : ' +'}</strong></div>
        <div><span>Est. CO₂</span><strong>{Math.round(totals.co2_g)} g</strong></div>
        <div><span>Walking</span><strong>{formatDistance(totals.walking_km) || '0 m'}</strong></div>
      </div>}
      <p className="muted-text">{plan.estimates_note}{totals && !totals.cost_complete ? ' Some fees are not verified, so the cost is a lower bound.' : ''}</p>
      {weather?.status === 'ok' && weather.signals?.length > 0 && <p className="muted-text">Planned around forecast: {weather.signals.join(', ')}.</p>}
      <SectionTitle eyebrow="Re-plan">Choose what matters</SectionTitle>
      <div className="replan-row">{PRESETS.filter((preset) => (config?.plan_presets || []).includes(preset.id)).map(({ id, label, Icon }) => <Chip key={id} active={plan.preset === id} disabled={busy} onClick={() => run(id, plan)}><Icon size={15} /> {label}</Chip>)}</div>
      <Notices items={[...(plan.warnings || []), ...notices]} />
      {plan.unscheduled?.length > 0 && <details className="unscheduled"><summary>{plan.unscheduled.length} places didn't fit</summary><ul>{plan.unscheduled.map((item) => <li key={item.id}>{item.name} — {item.reason}</li>)}</ul></details>}
    </>}
  </div>
}
