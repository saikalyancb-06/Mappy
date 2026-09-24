import { useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  BedDouble,
  Bookmark,
  Clock,
  Compass,
  Home,
  CalendarDays,
  Info,
  Lightbulb,
  MapPin,
  MessageCircle,
  Navigation,
  Route,
  ShieldCheck,
  Star,
  Ticket,
  Timer,
  UserRound,
  Wallet,
} from 'lucide-react'
import { formatDay, formatDistance, formatMinutes, formatMoney, openLabel, titleCase } from '../format'

const tabs = [
  { id: 'now', label: 'Explore', Icon: Home },
  { id: 'nearby', label: 'Nearby', Icon: Compass },
  { id: 'plan', label: 'Plan', Icon: Route },
  { id: 'ask', label: 'Ask', Icon: MessageCircle },
  { id: 'profile', label: 'Profile', Icon: UserRound },
]

export function IconCircleButton({ label, children, onClick, variant = 'light', disabled = false }) {
  return <button aria-label={label} title={label} className={`icon-circle ${variant}`} onClick={onClick} type="button" disabled={disabled}>{children}</button>
}

export function Chip({ active = false, children, onClick, disabled = false }) {
  return <button className={`chip ${active ? 'active' : ''}`} onClick={onClick} type="button" aria-pressed={active} disabled={disabled}>{children}</button>
}

export function SectionTitle({ eyebrow, children, action }) {
  return <div className="section-title-row"><div><span className="eyebrow">{eyebrow}</span><h2>{children}</h2></div>{action}</div>
}

export function WeatherPill({ weather }) {
  const ok = weather?.status === 'ok'
  const current = weather?.current
  const summary = ok ? (current ? `${Math.round(current.temperature_c)}°C · ${current.summary}` : `${weather.day?.summary}${weather.live === false ? ' (dataset)' : ''}`) : 'Unavailable'
  return <div className="weather-pill" title={ok ? (weather.live === false ? weather.note : `Open-Meteo, updated ${weather.retrieved_at || ''}`) : 'Live weather unavailable'}><span className="weather-symbol">{ok ? '☼' : '–'}</span><span><small>{weather?.live === false ? 'Weather (not live)' : 'Weather'}</small><strong>{summary}</strong></span></div>
}

const SOURCE_LABEL = { curated: 'Verified data', serpapi_maps: 'Live search', serpapi_hotels: 'Live rates', osm: 'OpenStreetMap', dataset: 'Organiser dataset', database: 'Stored data' }

export function SourceBadge({ place }) {
  const types = [...new Set((place.sources || []).map((source) => source.source_type))]
  const label = SOURCE_LABEL[types[0]] || 'Stored data'
  return <span className={`source-badge ${types[0] === 'curated' ? 'verified' : ''}`}>{label}{types.length > 1 ? ` +${types.length - 1}` : ''}</span>
}

export function ConfidenceBadge({ place }) {
  const label = place.confidence_detail?.label
  if (!label) return null
  return <span className={`confidence-badge conf-${label}`} title={Object.entries(place.confidence_detail.parts || {}).map(([key, part]) => `${key}: ${part.label} — ${part.note}`).join('\n')}><ShieldCheck size={12} /> {titleCase(label)} confidence</span>
}

export function CostChip({ place }) {
  const cost = place.cost_for_user || {}
  if (!cost.display && cost.kind !== 'unknown') return null
  const text = cost.kind === 'unknown' ? (place.kind === 'stay' ? 'Price n/a' : 'Cost unverified') : `${cost.display}${cost.kind === 'per_night' ? '/night' : ''}`
  const fit = cost.fits_budget === true ? 'fits' : cost.fits_budget === false ? 'over' : ''
  return <span className={`cost-chip ${fit}`} title={cost.note || ''}><Wallet size={12} />{text}{fit === 'fits' ? ' · in budget' : fit === 'over' ? ' · over budget' : ''}</span>
}

export function PlaceFacts({ place, showUserDistance = true }) {
  const open = openLabel(place)
  return <div className="place-meta">
    {place.distance_km != null && <span><Navigation size={13} />{formatDistance(place.distance_km)}</span>}
    {showUserDistance && place.distance_from_user_km != null && <span title="From your current location"><MapPin size={13} />{formatDistance(place.distance_from_user_km)} from you</span>}
    {place.detour_min != null ? <span><Timer size={13} />+{place.detour_min} min detour</span> : place.travel_min != null && place.travel_mode && <span title="Estimate from straight-line distance"><Timer size={13} />~{place.travel_min} min {place.travel_mode}</span>}
    {open && <span className={place.open_status === 'open' ? 'open-yes' : 'open-no'}><Clock size={13} />{open}</span>}
    <CostChip place={place} />
    {place.visit_duration_min != null && place.kind !== 'stay' && <span><Ticket size={13} />{formatMinutes(place.visit_duration_min)} visit</span>}
    {place.star_rating ? <span>{'★'.repeat(place.star_rating)}</span> : null}
    {place.guest_score != null ? <span><Star size={13} />{place.guest_score.toFixed(1)}/10{place.review_count ? ` (${place.review_count.toLocaleString()})` : ''}</span> : place.rating != null && <span><Star size={13} />{place.rating.toFixed(1)}{place.review_count ? ` (${place.review_count.toLocaleString()})` : ''}</span>}
    {place.step_free === true && <span>♿ Step-free</span>}
  </div>
}

export function ConflictNote({ place }) {
  if (!place.conflicts?.length) return null
  return <p className="conflict-note"><AlertTriangle size={14} /> Sources disagree: {place.conflicts.map((c) => c.detail).join('; ')}. Verify before travelling.</p>
}

export function PlaceCard({ place, featured = false, onOpen, saved = false, onSave }) {
  return <article className={`place-card ${featured ? 'featured' : ''}`}>
    <div className="place-art">{place.kind === 'stay' ? <BedDouble size={featured ? 34 : 26} /> : <MapPin size={featured ? 34 : 26} />}</div>
    <div className="place-content">
      <div className="place-heading">
        <div><span className="category-label">{titleCase(place.property_type || place.category || place.kind || 'place')} · <SourceBadge place={place} /></span><h3>{place.name}</h3></div>
        {onSave && <button className={`save-button ${saved ? 'saved' : ''}`} aria-label={`${saved ? 'Remove' : 'Save'} ${place.name}`} onClick={() => onSave(place)} type="button"><Bookmark size={18} fill={saved ? 'currentColor' : 'none'} /></button>}
      </div>
      <PlaceFacts place={place} />
      <ConfidenceBadge place={place} />
      <ConflictNote place={place} />
      {place.community?.from_feedback && place.community.top_vibes?.length > 0 && <p className="community-line">Visitors say: {place.community.top_vibes.map((v) => v.replace('_', ' ')).join(' · ')}</p>}
      {place.reasons?.length > 0 && <p>{place.reasons.slice(0, 2).join(' · ')}</p>}
      {onOpen && <button className="text-action" onClick={() => onOpen(place)} type="button">Why this place <ArrowRight size={16} /></button>}
    </div>
  </article>
}

const BAR_LABELS = { distance: 'Distance', rating: 'Rating', cost: 'Cost', quietness: 'Quietness', convenience: 'Convenience', fit: 'Fits you' }

export function WhyBars({ bars }) {
  if (!bars || !Object.keys(bars).length) return null
  return <div className="why-bars">{Object.entries(BAR_LABELS).filter(([key]) => bars[key] != null).map(([key, label]) => <div className="why-bar" key={key}>
    <span>{label}</span><div className="why-track" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(bars[key] * 100)} aria-label={label}><span style={{ width: `${Math.round(bars[key] * 100)}%` }} /></div>
  </div>)}</div>
}

export function ConfidenceList({ detail }) {
  if (!detail?.parts) return null
  return <ul className="confidence-list">{Object.entries(detail.parts).map(([key, part]) => <li key={key}><strong>{titleCase(key)}</strong><span className={`conf-${part.label}`}>{titleCase(part.label)}</span><small>{part.note}</small></li>)}</ul>
}

export function BottomTabBar({ activeTab, onChange }) {
  return <nav className="bottom-tabs" aria-label="Primary navigation">
    {tabs.map(({ id, label, Icon }) => <button key={id} className={activeTab === id ? 'active' : ''} onClick={() => onChange(id)} type="button" aria-label={label} aria-current={activeTab === id ? 'page' : undefined}>
      <Icon size={21} strokeWidth={1.8} /><span>{label}</span>
    </button>)}
  </nav>
}

export function StateMessage({ title, body, action }) {
  return <div className="state-message"><div className="state-icon"><MapPin size={22} /></div><h3>{title}</h3>{body && <p>{body}</p>}{action}</div>
}

export function Notices({ items }) {
  const list = [...new Set((items || []).filter(Boolean))]
  if (!list.length) return null
  return <div className="notices">{list.map((text) => <p key={text}><Info size={13} /> {text}</p>)}</div>
}

export function Understood({ items }) {
  if (!items?.length) return null
  return <div className="understood"><span className="eyebrow">Understood</span><div className="chip-row wrap">{items.map((item) => <span className="understood-chip" key={item}>{item}</span>)}</div></div>
}

const SEVERITY = { severe: 'Severe', high: 'High', warning: 'Warning', caution: 'Caution', moderate: 'Moderate', advisory: 'Advisory', low: 'Low', info: 'Info' }

export function AdvisoryList({ advisories }) {
  if (!advisories?.length) return <p className="muted-text">No active advisories are recorded for this area.</p>
  return <div className="advisory-list">{advisories.map((item) => <div className={`advisory severity-${item.severity}`} key={item.id}>
    <AlertTriangle size={16} />
    <div><strong>{item.title}</strong><span className="advisory-meta">{SEVERITY[item.severity] || item.severity} · {item.kind === 'weather_derived' ? 'from forecast' : item.issuing_body || item.source}{item.valid_to ? ` · until ${item.valid_to.slice(0, 10)}` : ''}{item.language && !item.language.startsWith('en') ? ` · in ${item.language}` : ''}</span>{item.body && <p>{item.body}</p>}</div>
  </div>)}</div>
}

// ---- events & tips ---------------------------------------------------------

const eventDates = (event) => (event.start_date === event.end_date ? formatDay(event.start_date) : `${formatDay(event.start_date, false)} → ${formatDay(event.end_date)}`)

const priceLabel = (event) => {
  const price = event.price || {}
  if (price.kind === 'free') return 'Free'
  if (price.kind === 'donation') return 'By donation'
  if (price.kind === 'paid') return price.min ? `From ${formatMoney(price.min, price.currency)}` : 'Ticketed'
  return event.is_ticketed ? (event.ticket_price ? `From ${formatMoney(event.ticket_price, event.currency)}` : 'Ticketed') : null
}

export function EventCard({ event }) {
  const [imageFailed, setImageFailed] = useState(false)
  const source = event.source || {}
  const start = event.start_date ? new Date(`${event.start_date}T12:00:00`) : null
  const checked = source.last_verified_at ? formatDay(source.last_verified_at.slice(0, 10)) : null
  const link = event.event_url || source.url || event.ticket_url
  const price = priceLabel(event)
  const unconfirmed = event.timing === 'usually_this_time_of_year'
  return <article className={`event-card ${event.type === 'festival' ? 'festival' : ''} ${event.confidence_label === 'low' ? 'low-confidence' : ''}`}>
    {event.image_url && !imageFailed ? <img className="event-image" src={event.image_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setImageFailed(true)} /> : <div className="event-date" aria-hidden="true">{start && <><strong>{start.getDate()}</strong><span>{start.toLocaleDateString('en-GB', { month: 'short' })}</span></>}</div>}
    <div className="event-body">
      <span className="category-label">{event.group_label}{event.type === 'festival' && event.category !== 'festivals' ? ' · festival' : ''}{event.multi_day ? ' · multi-day' : ''}</span>
      <h3>{event.name}</h3>
      <p className="event-meta"><CalendarDays size={13} />{unconfirmed ? 'Usually around this time — dates not confirmed' : `${eventDates(event)}${event.start_time ? ` · ${event.start_time}` : ''}`}{event.venue?.name ? ` · ${event.venue.name}` : ''}{event.distance_km != null ? ` · ${formatDistance(event.distance_km)} away` : ''}</p>
      {event.description && <p className="event-description">{event.description}</p>}
      {event.significance && <p><strong>Why it matters:</strong> {event.significance}</p>}
      {event.traditions && <p><strong>Traditions:</strong> {event.traditions}</p>}
      {event.etiquette && <p><strong>Etiquette:</strong> {event.etiquette}</p>}
      {(price || event.crowded) && <p className="event-meta"><Ticket size={13} />{[price, event.crowded ? 'Large crowds expected' : null].filter(Boolean).join(' · ')}</p>}
      {event.confidence_label === 'low' && <p className="event-warning">Unverified listing — check the source before going.</p>}
      {event.freshness === 'stale' && <p className="event-warning">This listing hasn't been re-checked recently.</p>}
      <small className="event-source">Source: {source.name || 'stored record'}{event.sources?.length > 1 ? ` + ${event.sources.length - 1} more` : ''}{checked ? ` · checked ${checked}` : ''}</small>
      {link && <div className="event-actions"><a className="secondary-button small" href={link} target="_blank" rel="noopener noreferrer">Open event page</a>{event.ticket_url && event.ticket_url !== link && <a className="link-button" href={event.ticket_url} target="_blank" rel="noopener noreferrer">Tickets</a>}</div>}
    </div>
  </article>
}

export function TipList({ tips }) {
  if (!tips?.length) return <p className="muted-text">No tips for this date from the available data.</p>
  return <ul className="tip-list">{tips.map((tip) => <li key={tip.id} className={`tip-${tip.kind}`}><Lightbulb size={15} /><div><p>{tip.text}</p><small>Based on: {tip.basis}</small></div></li>)}</ul>
}
