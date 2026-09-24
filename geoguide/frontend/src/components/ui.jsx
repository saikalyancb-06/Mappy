import {
  ArrowRight,
  Bookmark,
  Compass,
  Home,
  MapPin,
  MessageCircle,
  Navigation,
  Route,
  UserRound,
} from 'lucide-react'

const icons = { now: Home, nearby: Compass, plan: Route, ask: MessageCircle, profile: UserRound }

export function IconCircleButton({ label, children, onClick, variant = 'light' }) {
  return <button aria-label={label} className={`icon-circle ${variant}`} onClick={onClick} type="button">{children}</button>
}

export function Chip({ active = false, children, onClick }) {
  return <button className={`chip ${active ? 'active' : ''}`} onClick={onClick} type="button">{children}</button>
}

export function SectionTitle({ eyebrow, children, action }) {
  return <div className="section-title-row"><div><span className="eyebrow">{eyebrow}</span><h2>{children}</h2></div>{action}</div>
}

export function WeatherPill({ weather }) {
  const summary = weather?.summary || 'Weather unavailable'
  return <div className="weather-pill"><span className="weather-symbol">◌</span><span><small>Weather</small><strong>{summary}</strong></span></div>
}

export function PlaceCard({ place, featured = false, onOpen, saved = false, onSave }) {
  return <article className={`place-card ${featured ? 'featured' : ''}`}>
    {place.image ? <img src={place.image} alt="" loading="lazy" /> : <div className="place-art"><MapPin size={28} /></div>}
    <div className="place-content">
      <div className="place-heading"><div><span className="category-label">{place.category}</span><h3>{place.name}</h3></div><button className={`save-button ${saved ? 'saved' : ''}`} aria-label={`${saved ? 'Remove' : 'Save'} ${place.name}`} onClick={() => onSave?.(place)} type="button"><Bookmark size={18} fill={saved ? 'currentColor' : 'none'} /></button></div>
      <div className="place-meta">
        {place.distance && <span><Navigation size={14} />{place.distance}</span>}
        {place.score != null && <span className="match"><span>{place.score}%</span> match</span>}
        {place.rating != null && <span>{place.rating} rating</span>}
      </div>
      {place.reasons?.[0] && <p>{place.reasons[0]}</p>}
      {place.openingHours && <p>{place.openingHours}</p>}
      {onOpen && <button className="text-action" onClick={onOpen} type="button">View place <ArrowRight size={16} /></button>}
    </div>
  </article>
}

export function BottomTabBar({ activeTab, onChange }) {
  return <nav className="bottom-tabs" aria-label="Primary navigation">
    {Object.entries(icons).map(([id, Icon]) => <button key={id} className={activeTab === id ? 'active' : ''} onClick={() => onChange(id)} type="button" aria-label={id}>
      <Icon size={21} strokeWidth={1.8} /><span>{id[0].toUpperCase() + id.slice(1)}</span>
    </button>)}
  </nav>
}

export function StateMessage({ title, body, action }) {
  return <div className="state-message"><div className="state-icon"><MapPin size={22} /></div><h3>{title}</h3><p>{body}</p>{action}</div>
}
