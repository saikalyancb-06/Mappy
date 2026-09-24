import {
  AlertTriangle,
  ArrowRight,
  Bookmark,
  Clock,
  Compass,
  Home,
  Info,
  MapPin,
  MessageCircle,
  Navigation,
  Route,
  Star,
  Ticket,
  UserRound,
} from 'lucide-react'
import { formatDistance, formatMinutes, formatMoney, openLabel, titleCase } from '../format'

const tabs = [
  { id: 'now', label: 'Now', Icon: Home },
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
  const summary = ok ? (current ? `${Math.round(current.temperature_c)}°C · ${current.summary}` : weather.day?.summary) : 'Unavailable'
  return <div className="weather-pill" title={ok ? `Open-Meteo, updated ${weather.retrieved_at || ''}` : 'Live weather unavailable'}><span className="weather-symbol">{ok ? '☼' : '–'}</span><span><small>Weather</small><strong>{summary}</strong></span></div>
}

export function SourceBadge({ place }) {
  const types = new Set((place.sources || []).map((source) => source.source_type))
  const label = types.has('curated') ? 'Verified data' : types.has('serpapi_maps') ? 'Live search' : types.has('osm') ? 'OpenStreetMap' : 'Stored data'
  return <span className={`source-badge ${types.has('curated') ? 'verified' : ''}`}>{label}{types.size > 1 ? ` +${types.size - 1}` : ''}</span>
}

export function PlaceFacts({ place, showUserDistance = true }) {
  const fee = formatMoney(place.entry_fee, place.fee_currency)
  const open = openLabel(place)
  return <div className="place-meta">
    {place.distance_km != null && <span><Navigation size={13} />{formatDistance(place.distance_km)}</span>}
    {showUserDistance && place.distance_from_user_km != null && <span title="From your current location"><MapPin size={13} />{formatDistance(place.distance_from_user_km)} from you</span>}
    {open && <span className={place.open_status === 'open' ? 'open-yes' : 'open-no'}><Clock size={13} />{open}</span>}
    {fee && <span><Ticket size={13} />{fee}</span>}
    {place.visit_duration_min != null && <span>{formatMinutes(place.visit_duration_min)} visit</span>}
    {place.rating != null && <span><Star size={13} />{place.rating.toFixed(1)}{place.review_count ? ` (${place.review_count.toLocaleString()})` : ''}</span>}
    {place.step_free === true && <span>♿ Step-free</span>}
  </div>
}

export function PlaceCard({ place, featured = false, onOpen, saved = false, onSave }) {
  return <article className={`place-card ${featured ? 'featured' : ''}`}>
    <div className="place-art"><MapPin size={featured ? 34 : 26} /></div>
    <div className="place-content">
      <div className="place-heading">
        <div><span className="category-label">{titleCase(place.category || place.kind || 'place')} · <SourceBadge place={place} /></span><h3>{place.name}</h3></div>
        {onSave && <button className={`save-button ${saved ? 'saved' : ''}`} aria-label={`${saved ? 'Remove' : 'Save'} ${place.name}`} onClick={() => onSave(place)} type="button"><Bookmark size={18} fill={saved ? 'currentColor' : 'none'} /></button>}
      </div>
      <PlaceFacts place={place} />
      {place.reasons?.length > 0 && <p>{place.reasons.slice(0, 2).join(' · ')}</p>}
      {onOpen && <button className="text-action" onClick={() => onOpen(place)} type="button">View place <ArrowRight size={16} /></button>}
    </div>
  </article>
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
  const list = (items || []).filter(Boolean)
  if (!list.length) return null
  return <div className="notices">{list.map((text) => <p key={text}><Info size={13} /> {text}</p>)}</div>
}

const SEVERITY = { high: 'High', moderate: 'Moderate', low: 'Low', info: 'Info' }

export function AdvisoryList({ advisories }) {
  if (!advisories?.length) return <p className="muted-text">No active advisories are recorded for this area.</p>
  return <div className="advisory-list">{advisories.map((item) => <div className={`advisory severity-${item.severity}`} key={item.id}>
    <AlertTriangle size={16} />
    <div><strong>{item.title}</strong><span className="advisory-meta">{SEVERITY[item.severity] || item.severity} · {item.kind === 'weather_derived' ? 'from forecast' : item.source}</span>{item.body && <p>{item.body}</p>}</div>
  </div>)}</div>
}
