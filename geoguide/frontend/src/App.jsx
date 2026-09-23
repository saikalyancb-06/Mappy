import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronRight, LocateFixed, LogOut, Mic, RefreshCw, Send, Sparkles } from 'lucide-react'
import { askGeoGuide, getNearby, getNow, logIn, logOut, normalizePlace, signUp, startLocationIngestion } from './api'
import { interestOptions } from './config'
import { BottomTabBar, Chip, IconCircleButton, PlaceCard, SectionTitle, StateMessage, WeatherPill } from './components/ui'
import './App.css'

const readStoredInterests = () => {
  try {
    const stored = JSON.parse(localStorage.getItem('geoguide-interests') || '[]')
    return Array.isArray(stored) ? stored.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

function Onboarding({ onComplete }) {
  const [selected, setSelected] = useState([])
  return <div className="onboarding-screen">
  <div className="progress-dots"><span className="active" /><span /><span /></div>
  <div className="onboarding-copy"><span className="brand-mark">G</span><span className="eyebrow">Your place companion</span><h1>What makes a place feel right?</h1><p>Choose a few interests. GeoGuide will use them to shape recommendations around your time and location.</p></div>
  <div className="option-grid">{interestOptions.map((option) => <Chip key={option.id} active={selected.includes(option.id)} onClick={() => setSelected((current) => current.includes(option.id) ? current.filter((item) => item !== option.id) : [...current, option.id])}>{selected.includes(option.id) && <Check size={16} />}{option.label}</Chip>)}</div>
  <button className="primary-button onboarding-continue" disabled={!selected.length} onClick={() => onComplete(selected)} type="button">Continue <ChevronRight size={18} /></button>
  <button className="skip-button" onClick={() => onComplete([])} type="button">Skip for now</button>
  </div>
}

function AuthView({ onAuthenticated }) {
  const [mode, setMode] = useState('signup')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event) => {
    event.preventDefault()
    setError('')
    if (!email.includes('@') || password.length < 6) { setError('Use a valid email and a password with at least 6 characters.'); return }
    setBusy(true)
    try {
      const result = mode === 'signup' ? await signUp(name, email, password) : await logIn(email, password)
      localStorage.setItem('geoguide-token', result.token)
      localStorage.setItem('geoguide-account', JSON.stringify(result.user))
      localStorage.setItem('geoguide-session', 'true')
      onAuthenticated()
    } catch (requestError) {
      setError(requestError.message || 'Authentication failed.')
    } finally { setBusy(false) }
  }
  return <div className="auth-screen"><div className="auth-brand"><span className="brand-mark">G</span><span className="eyebrow">Your place companion</span></div><div className="auth-copy"><h1>{mode === 'signup' ? 'Make every place feel closer.' : 'Welcome back.'}</h1><p>{mode === 'signup' ? 'Create your GeoGuide account to keep preferences and plans with you.' : 'Sign in to continue your local guide.'}</p></div><div className="auth-tabs"><button className={mode === 'signup' ? 'active' : ''} onClick={() => { setMode('signup'); setError('') }} type="button">Sign up</button><button className={mode === 'login' ? 'active' : ''} onClick={() => { setMode('login'); setError('') }} type="button">Log in</button></div><form className="auth-form" onSubmit={submit}>{mode === 'signup' && <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Your name" autoComplete="name" /></label>}<label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" autoComplete="email" required /></label><label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 6 characters" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="primary-button" disabled={busy} type="submit">{busy ? 'Connecting…' : mode === 'signup' ? 'Create account' : 'Log in'} <ChevronRight size={18} /></button></form><p className="auth-note">Your account is stored securely by the GeoGuide backend.</p></div>
}

function PermissionView({ onLocate, busy }) {
  return <div className="permission-screen"><div className="permission-icon"><LocateFixed size={38} /></div><span className="eyebrow">A better local view</span><h1>Let’s find what is near you.</h1><p>GeoGuide uses your location to resolve the place, weather, and nearby recommendations. You can also explore with a destination later.</p><button className="primary-button" onClick={onLocate} disabled={busy} type="button">{busy ? 'Finding your place…' : 'Allow location'} <LocateFixed size={18} /></button><button className="secondary-button" type="button">Choose a destination</button></div>
}

function PlaceDetailView({ place, saved, onSave, onBack, now }) {
  return <div className="detail-view"><header className="detail-header"><IconCircleButton label="Back to nearby" onClick={onBack}>‹</IconCircleButton><span className="status-pill">{place.source || 'Source unavailable'}</span></header><div className="detail-art">{place.image ? <img src={place.image} alt="" /> : <div className="place-art"><LocateFixed size={44} /></div>}<div className="detail-title"><span className="category-label">{place.category}</span><h1>{place.name}</h1></div></div><section className="detail-sheet"><button className={`detail-save ${saved ? 'saved' : ''}`} aria-label="Save place" onClick={() => onSave(place)} type="button">★</button><div className="place-meta">{place.distance && <span>{place.distance} away</span>}{place.score != null && <span>{place.score}% match</span>}</div><h2>Why this suits you</h2><p>{place.reasons?.join(' · ') || 'A personalized reason will appear when recommendation context is available.'}</p><h2>About</h2><p>Descriptions, opening hours, accessibility, and costs are shown only when verified place data is available.</p><div className="detail-actions"><button className="secondary-button" onClick={() => onSave(place)} type="button">{saved ? 'Saved' : 'Add to plan'}</button><button className="primary-button" onClick={() => window.open(`https://www.google.com/maps/dir/?api=1&destination=${place.lat || now?.location?.lat || ''},${place.lon || now?.location?.lon || ''}`, '_blank', 'noopener,noreferrer')} type="button">Navigate</button></div></section></div>
}

function NowView({ now, places, loading, error, onRetry, onOpen }) {
  const place = places[0]
  const areaName = now?.area?.name || now?.location?.city || 'Your area'
  return <div className="view-content">
  <header className="app-header"><div><span className="eyebrow">Good to see you</span><h1>Hi there <span className="wave">👋</span></h1></div><WeatherPill weather={now?.weather} /></header>
  <section className="destination-heading"><span className="country-label">Current location</span><h2>{areaName}</h2><p>{now?.local_time ? new Date(now.local_time).toLocaleString([], { weekday: 'long', hour: 'numeric', minute: '2-digit' }) : 'Live context is loading'}</p></section>
  <div className="chip-row"><Chip active>For you</Chip><Chip>Nature</Chip><Chip>Heritage</Chip><Chip>Food</Chip></div>
  {loading && <div className="skeleton-card" />}
  {!loading && error && <StateMessage title="Could not load your guide" body={error} action={<button className="secondary-button" onClick={onRetry} type="button"><RefreshCw size={16} /> Try again</button>} />}
  {!loading && !error && place && <PlaceCard place={place} featured onOpen={() => onOpen(place)} />}
  {!loading && !error && !place && <StateMessage title="No nearby places yet" body="Once local place data is available, your recommendations will appear here." />}
  <section className="briefing-card"><div className="verified-row"><span className="verified-dot" /> Context briefing <span className="verified-badge">Verified context</span></div><p>{now?.briefing || 'Your local briefing will appear when live context is available.'}</p></section>
  <SectionTitle eyebrow="Today" action={<span className="muted-label">Live context</span>}>A little more useful</SectionTitle>
  <div className="context-grid"><div><span className="context-icon">☼</span><span>Daylight</span><strong>{now?.daylight_left || 'Unknown'}</strong></div><div><span className="context-icon">⌁</span><span>Advisories</span><strong>{now?.advisories?.length ? `${now.advisories.length} active` : 'None reported'}</strong></div></div>
  </div>
}

function NearbyView({ places, savedPlaces, onSave, onOpen, onRefresh }) {
  return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Around you</span><h1>Nearby</h1></div><IconCircleButton label="Refresh nearby" onClick={onRefresh}><RefreshCw size={20} /></IconCircleButton></header><div className="segmented"><button className="active" type="button">Attractions</button><button type="button">Hotels</button></div><div className="chip-row"><Chip active>Now</Chip><Chip>2 hours</Chip><Chip>4 hours</Chip><Chip>Full day</Chip></div><SectionTitle eyebrow="Highest match">Places worth your time</SectionTitle><div className="place-list">{places.length ? places.map((place) => <PlaceCard key={place.id} place={place} saved={savedPlaces.includes(place.id)} onSave={onSave} onOpen={() => onOpen(place)} />) : <StateMessage title="Nothing nearby yet" body="Try refreshing after location data has finished loading." />}</div></div>
}

function PlanView({ places }) {
  return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Shape the day</span><h1>Your plan</h1></div><span className="status-pill">Draft</span></header><div className="chip-row"><Chip active>2 hours</Chip><Chip>4 hours</Chip><Chip>Full day</Chip></div>{places.length ? <div className="timeline-card">{places.slice(0, 3).map((place, index) => <div className="timeline-stop" key={place.id}><span className="stop-number">{index + 1}</span><div><span className="eyebrow">{index === 0 ? 'Start nearby' : 'Next stop'}</span><h3>{place.name}</h3><p>{place.distance || 'Travel time unknown'} · {place.category}</p></div></div>)}</div> : <StateMessage title="Your plan is empty" body="Save a nearby place and it will appear here." />}<SectionTitle eyebrow="Re-plan">Choose what matters</SectionTitle><div className="replan-row"><Chip>Cheaper</Chip><Chip>Greener</Chip><Chip>Less walking</Chip></div></div>
}

function AskView({ now }) {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const send = async (event) => { event.preventDefault(); if (!question.trim() || sending) return; const next = question.trim(); setQuestion(''); setMessages((current) => [...current, { role: 'user', text: next }]); setSending(true); try { const answer = await askGeoGuide(next, now?.location); setMessages((current) => [...current, { role: 'assistant', text: answer.answer || answer.message || 'No answer was returned.', verified: answer.verification || 'Unverified' }]) } catch { setMessages((current) => [...current, { role: 'assistant', text: 'I could not reach the guide right now. Try again when the connection is back.', verified: 'Unverified' }]) } finally { setSending(false) } }
  return <div className="view-content ask-view"><header className="simple-header"><div><span className="eyebrow">A grounded conversation</span><h1>Ask GeoGuide</h1></div><IconCircleButton label="Voice input"><Mic size={20} /></IconCircleButton></header>{!messages.length && <div className="ask-intro"><Sparkles size={26} /><h2>What do you want to know?</h2><p>Ask about nearby places, timing, routes, or what suits your travel style.</p><div className="suggestion-row"><Chip onClick={() => setQuestion('What is good nearby right now?')}>What is nearby?</Chip><Chip onClick={() => setQuestion('What suits the next two hours?')}>Next two hours</Chip></div></div>}<div className="message-list">{messages.map((message, index) => <div className={`message ${message.role}`} key={`${message.role}-${index}`}><p>{message.text}</p>{message.verified && <small>{message.verified}</small>}</div>)}</div><form className="ask-composer" onSubmit={send}><input aria-label="Ask GeoGuide" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about this place…" /><button aria-label="Send question" className="send-button" disabled={sending} type="submit"><Send size={18} /></button></form></div>
}

function ProfileView({ interests, onReset, onLogout }) {
  return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Your preferences</span><h1>Profile</h1></div><div className="avatar">{interests.length ? interests[0][0].toUpperCase() : '?'}</div></header><div className="profile-card"><div className="profile-avatar">{interests.length ? interests[0][0].toUpperCase() : '?'}</div><h2>Local explorer</h2><p>Your recommendations adapt as your interests change.</p></div><SectionTitle eyebrow="Interests">What you care about</SectionTitle><div className="chip-row wrap">{(interests.length ? interests : ['nature', 'heritage']).map((interest) => <Chip active key={interest}>{interest}</Chip>)}</div><button className="secondary-button full-width" onClick={onReset} type="button">Run onboarding again</button><button className="logout-button" onClick={onLogout} type="button"><LogOut size={17} /> Log out</button></div>
}

function UIKitView() {
  const samplePlace = { id: 'ui-sample', name: 'Example place from API', category: 'place', distance: '1.2 km', score: 86, reasons: ['Close to your current location'] }
  return <div className="app-frame"><main className="app-scroll ui-kit"><header className="simple-header"><div><span className="eyebrow">Development route</span><h1>UI kit</h1></div><span className="status-pill">Preview</span></header><SectionTitle eyebrow="Controls">Chips and actions</SectionTitle><div className="chip-row"><Chip active>Selected</Chip><Chip>Available</Chip><IconCircleButton label="Example action"><RefreshCw size={20} /></IconCircleButton></div><SectionTitle eyebrow="Cards">Place states</SectionTitle><PlaceCard place={samplePlace} /><PlaceCard place={samplePlace} featured /><div className="skeleton-card" /><StateMessage title="Empty state" body="Unavailable values stay explicit and do not become invented content." /><button className="primary-button full-width" type="button">Primary action <ChevronRight size={18} /></button></main></div>
}

function App() {
  const [authenticated, setAuthenticated] = useState(() => localStorage.getItem('geoguide-session') === 'true' && Boolean(localStorage.getItem('geoguide-token')))
  const [onboarded, setOnboarded] = useState(() => localStorage.getItem('geoguide-onboarded') === 'true')
  const [interests, setInterests] = useState(readStoredInterests)
  const [permission, setPermission] = useState(() => localStorage.getItem('geoguide-location') !== 'true')
  const [location, setLocation] = useState(() => { try { return JSON.parse(localStorage.getItem('geoguide-coordinates') || 'null') } catch { return null } })
  const [activeTab, setActiveTab] = useState('now')
  const [selectedPlace, setSelectedPlace] = useState(null)
  const [savedPlaces, setSavedPlaces] = useState(() => { try { const value = JSON.parse(localStorage.getItem('geoguide-saved') || '[]'); return Array.isArray(value) ? value : [] } catch { return [] } })
  const [now, setNow] = useState(null)
  const [places, setPlaces] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const loadData = useCallback(async () => { setLoading(true); setError(''); try { const [nowData, nearbyData] = await Promise.all([getNow(location), getNearby(location)]); setNow(nowData); setPlaces((nearbyData.items || []).map(normalizePlace)) } catch (requestError) { setError(requestError.message || 'The guide is temporarily unavailable.') } finally { setLoading(false) } }, [location])
  useEffect(() => {
    if (!onboarded || permission) return undefined
    const timer = window.setTimeout(() => { loadData() }, 0)
    return () => window.clearTimeout(timer)
  }, [onboarded, permission, loadData])
  const completeOnboarding = (chosen) => { setInterests(chosen); localStorage.setItem('geoguide-interests', JSON.stringify(chosen)); localStorage.setItem('geoguide-onboarded', 'true'); setOnboarded(true) }
  const locate = () => { setPermission(false); localStorage.setItem('geoguide-location', 'true'); if (navigator.geolocation) navigator.geolocation.getCurrentPosition((position) => { const coordinates = { lat: position.coords.latitude, lon: position.coords.longitude }; setLocation(coordinates); localStorage.setItem('geoguide-coordinates', JSON.stringify(coordinates)); startLocationIngestion({ ...coordinates, accuracy: position.coords.accuracy }).catch(() => {}) }, () => {}) }
  const toggleSaved = (place) => { setSavedPlaces((current) => { const next = current.includes(place.id) ? current.filter((id) => id !== place.id) : [...current, place.id]; localStorage.setItem('geoguide-saved', JSON.stringify(next)); return next }) }
  const logout = async () => { await logOut().catch(() => undefined); localStorage.removeItem('geoguide-session'); localStorage.removeItem('geoguide-token'); setAuthenticated(false) }
  if (window.location.pathname === '/dev/ui-kit') return <UIKitView />
  if (!authenticated) return <AuthView onAuthenticated={() => setAuthenticated(true)} />
  if (!onboarded) return <Onboarding onComplete={completeOnboarding} />
  if (permission) return <PermissionView onLocate={locate} busy={false} />
  if (selectedPlace) return <div className="app-frame"><main className="app-scroll detail-scroll"><PlaceDetailView place={selectedPlace} saved={savedPlaces.includes(selectedPlace.id)} onSave={toggleSaved} onBack={() => setSelectedPlace(null)} now={now} /></main></div>
  const content = { now: <NowView now={now} places={places} loading={loading} error={error} onRetry={loadData} onOpen={setSelectedPlace} />, nearby: <NearbyView places={places} savedPlaces={savedPlaces} onSave={toggleSaved} onOpen={setSelectedPlace} onRefresh={loadData} />, plan: <PlanView places={places.filter((place) => savedPlaces.includes(place.id))} />, ask: <AskView now={now} />, profile: <ProfileView interests={interests} onReset={() => { localStorage.removeItem('geoguide-onboarded'); setOnboarded(false) }} onLogout={logout} /> }[activeTab]
  return <div className="app-frame"><main className="app-scroll">{content}</main><BottomTabBar activeTab={activeTab} onChange={setActiveTab} /></div>
}

export default App
