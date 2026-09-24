import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronRight, LocateFixed, LogOut, Mic, RefreshCw, Send, Sparkles } from 'lucide-react'
import { askGeoGuide, getIngestionStatus, getNearby, getNow, logIn, logOut, normalizePlace, recordInteraction, signUp, startLocationIngestion, updatePreferences } from './api'
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

const readStoredLocation = () => {
  try {
    const stored = JSON.parse(localStorage.getItem('geoguide-coordinates') || 'null')
    return stored && (stored.lat != null && stored.lon != null || stored.city) ? stored : null
  } catch {
    return null
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

function PermissionView({ onLocate, onDestination, busy, error }) {
  const [destination, setDestination] = useState('')
  const submitDestination = (event) => {
    event.preventDefault()
    if (destination.trim()) onDestination(destination.trim())
  }
  return <div className="permission-screen"><div className="permission-icon"><LocateFixed size={38} /></div><span className="eyebrow">Start with a destination</span><h1>Where do you want to explore?</h1><p>Enter any city or place to start building a local knowledge base in the background. You can also use your current location.</p>{error && <p className="form-error" role="alert">{error}</p>}<form className="destination-form" onSubmit={submitDestination}><label>Destination<input value={destination} onChange={(event) => setDestination(event.target.value)} placeholder="City or place" autoFocus /></label><button className="primary-button" disabled={!destination.trim()} type="submit">Explore destination <ChevronRight size={18} /></button></form><button className="secondary-button" onClick={onLocate} disabled={busy} type="button">{busy ? 'Finding your place…' : 'Use my current location'} <LocateFixed size={18} /></button></div>
}

function PlaceDetailView({ place, saved, onSave, onBack, now }) {
  return <div className="detail-view"><header className="detail-header"><IconCircleButton label="Back to nearby" onClick={onBack}>‹</IconCircleButton><span className="status-pill">{place.source || 'Source unavailable'}</span></header><div className="detail-art">{place.image ? <img src={place.image} alt="" /> : <div className="place-art"><LocateFixed size={44} /></div>}<div className="detail-title"><span className="category-label">{place.category}</span><h1>{place.name}</h1></div></div><section className="detail-sheet"><button className={`detail-save ${saved ? 'saved' : ''}`} aria-label="Save place" onClick={() => onSave(place)} type="button">★</button><div className="place-meta">{place.distance && <span>{place.distance} away</span>}{place.score != null && <span>{place.score}% match</span>}</div><h2>Why this suits you</h2><p>{place.reasons?.join(' · ') || 'A personalized reason will appear when recommendation context is available.'}</p><h2>About</h2><p>Descriptions, opening hours, accessibility, and costs are shown only when verified place data is available.</p><div className="detail-actions"><button className="secondary-button" onClick={() => onSave(place)} type="button">{saved ? 'Saved' : 'Add to plan'}</button><button className="primary-button" onClick={() => window.open(`https://www.google.com/maps/dir/?api=1&destination=${place.lat || now?.location?.lat || ''},${place.lon || now?.location?.lon || ''}`, '_blank', 'noopener,noreferrer')} type="button">Navigate</button></div></section></div>
}

function NowView({ now, places, loading, error, onRetry, onOpen, onSave, savedPlaces = [], selectedCategory = 'for_you', onSelectCategory, ingestion }) {
  const areaName = now?.area?.name || now?.location?.city || 'Your area'
  const filteredPlaces = places.filter((p) => {
    if (!selectedCategory || selectedCategory === 'for_you') return true
    const cat = `${p.category || ''} ${p.name || ''}`.toLowerCase()
    if (selectedCategory === 'nature') return /nature|park|garden|lake|green|scenic|outdoor/.test(cat)
    if (selectedCategory === 'heritage') return /heritage|historic|history|museum|temple|monument|fort|palace/.test(cat)
    if (selectedCategory === 'food') return /food|restaurant|cafe|bakery|dining|eatery/.test(cat)
    return true
  })
  const place = filteredPlaces[0] || places[0]

  return <div className="view-content">
  <header className="app-header"><div><span className="eyebrow">Good to see you</span><h1>Hi there <span className="wave">👋</span></h1></div><WeatherPill weather={now?.weather} /></header>
  <section className="destination-heading"><span className="country-label">Current location</span><h2>{areaName}</h2><p>{now?.local_time ? new Date(now.local_time).toLocaleString([], { weekday: 'long', hour: 'numeric', minute: '2-digit' }) : 'Live context is loading'}</p></section>
  <div className="chip-row">
    <Chip active={selectedCategory === 'for_you'} onClick={() => onSelectCategory('for_you')}>For you</Chip>
    <Chip active={selectedCategory === 'nature'} onClick={() => onSelectCategory('nature')}>Nature</Chip>
    <Chip active={selectedCategory === 'heritage'} onClick={() => onSelectCategory('heritage')}>Heritage</Chip>
    <Chip active={selectedCategory === 'food'} onClick={() => onSelectCategory('food')}>Food</Chip>
  </div>
  {ingestion && <section className="ingestion-card"><div className="verified-row"><span className="verified-dot" /> City knowledge <strong>{ingestion.progress || 0}%</strong></div><div className="progress-track"><span style={{ width: `${ingestion.progress || 0}%` }} /></div><p>{ingestion.status === 'failed' ? ingestion.error || 'Some city sources could not be loaded.' : `Building local knowledge: ${(ingestion.step || 'starting').replaceAll('_', ' ')}.`}</p></section>}
  {loading && <div className="skeleton-card" />}
  {!loading && error && <StateMessage title="Could not load your guide" body={error} action={<button className="secondary-button" onClick={onRetry} type="button"><RefreshCw size={16} /> Try again</button>} />}
  {!loading && !error && place && <PlaceCard place={place} featured onOpen={() => onOpen(place)} saved={savedPlaces.includes(place.id)} onSave={onSave} />}
  {!loading && !error && !place && <StateMessage title="No nearby places yet" body="Once local place data is available, your recommendations will appear here." />}
  <section className="briefing-card"><div className="verified-row"><span className="verified-dot" /> Context briefing <span className="verified-badge">Verified context</span></div><p>{now?.briefing || 'Your local briefing will appear when live context is available.'}</p></section>
  <SectionTitle eyebrow="Today" action={<span className="muted-label">Live context</span>}>A little more useful</SectionTitle>
  <div className="context-grid"><div><span className="context-icon">☼</span><span>Daylight</span><strong>{now?.daylight_left || 'Unknown'}</strong></div><div><span className="context-icon">⌁</span><span>Advisories</span><strong>{now?.advisories?.length ? `${now.advisories.length} active` : 'None reported'}</strong></div></div>
  </div>
}

function NearbyView({ places, savedPlaces, onSave, onOpen, onRefresh, activeCategory = 'all', onCategoryChange, activeTime = 'now', onTimeChange }) {
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">Around you</span><h1>Nearby</h1></div><IconCircleButton label="Refresh nearby" onClick={onRefresh}><RefreshCw size={20} /></IconCircleButton></header>
    <div className="segmented">
      <button className={activeCategory === 'attractions' ? 'active' : ''} onClick={() => onCategoryChange(activeCategory === 'attractions' ? 'all' : 'attractions')} type="button">Attractions</button>
      <button className={activeCategory === 'hotels' ? 'active' : ''} onClick={() => onCategoryChange(activeCategory === 'hotels' ? 'all' : 'hotels')} type="button">Hotels</button>
    </div>
    <div className="chip-row">
      <Chip active={activeTime === 'now'} onClick={() => onTimeChange('now')}>Now</Chip>
      <Chip active={activeTime === '2h'} onClick={() => onTimeChange('2h')}>2 hours</Chip>
      <Chip active={activeTime === '4h'} onClick={() => onTimeChange('4h')}>4 hours</Chip>
      <Chip active={activeTime === 'full'} onClick={() => onTimeChange('full')}>Full day</Chip>
    </div>
    <SectionTitle eyebrow="Highest match">Places worth your time</SectionTitle>
    <div className="place-list">
      {places.length ? places.map((place) => <PlaceCard key={place.id} place={place} saved={savedPlaces.includes(place.id)} onSave={onSave} onOpen={() => onOpen(place)} />) : <StateMessage title="Nothing nearby yet" body="Try refreshing or switching categories to find places." />}
    </div>
  </div>
}

function PlanView({ places }) {
  return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Shape the day</span><h1>Your plan</h1></div><span className="status-pill">Draft</span></header><div className="chip-row"><Chip active>2 hours</Chip><Chip>4 hours</Chip><Chip>Full day</Chip></div>{places.length ? <div className="timeline-card">{places.slice(0, 3).map((place, index) => <div className="timeline-stop" key={place.id}><span className="stop-number">{index + 1}</span><div><span className="eyebrow">{index === 0 ? 'Start nearby' : 'Next stop'}</span><h3>{place.name}</h3><p>{place.distance || 'Travel time unknown'} · {place.category}</p></div></div>)}</div> : <StateMessage title="Your plan is empty" body="Save a nearby place and it will appear here." />}<SectionTitle eyebrow="Re-plan">Choose what matters</SectionTitle><div className="replan-row"><Chip>Cheaper</Chip><Chip>Greener</Chip><Chip>Less walking</Chip></div></div>
}

function AskView({ locationContext }) {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const send = async (event) => { event.preventDefault(); if (!question.trim() || sending) return; const next = question.trim(); setQuestion(''); setMessages((current) => [...current, { role: 'user', text: next }]); setSending(true); try { const answer = await askGeoGuide(next, locationContext); const liveError = answer.diagnostics?.web_error?.message; setMessages((current) => [...current, { role: 'assistant', text: answer.answer || answer.message || 'No answer was returned.', verified: answer.verification_status || 'Unverified', results: answer.results || [], notice: liveError && !answer.results?.length ? `Live search: ${liveError}` : '' }]) } catch (requestError) { setMessages((current) => [...current, { role: 'assistant', text: requestError.message || 'I could not reach the guide right now. Try again when the connection is back.', verified: 'Unavailable' }]) } finally { setSending(false) } }
  return <div className="view-content ask-view"><header className="simple-header"><div><span className="eyebrow">A grounded conversation</span><h1>Ask GeoGuide</h1></div><IconCircleButton label="Voice input"><Mic size={20} /></IconCircleButton></header>{!messages.length && <div className="ask-intro"><Sparkles size={26} /><h2>What do you want to know?</h2><p>Ask about nearby places, timing, routes, or what suits your travel style.</p><div className="suggestion-row"><Chip onClick={() => setQuestion('What is good nearby right now?')}>What is nearby?</Chip><Chip onClick={() => setQuestion('What suits the next two hours?')}>Next two hours</Chip></div></div>}<div className="message-list">{messages.map((message, index) => <div className={`message ${message.role}`} key={`${message.role}-${index}`}><p>{message.text}</p>{message.verified && <small>{message.verified}</small>}{message.notice && <small className="form-error">{message.notice}</small>}{message.results?.length > 0 && <div className="ask-results">{message.results.slice(0, 6).map((result) => <a className="ask-result" href={result.source_url || result.url} target="_blank" rel="noreferrer" key={result.source_url || result.url || result.name}><strong>{result.name || result.title}</strong><span>{result.category || result.source || 'Web result'}{result.distance_km != null ? ` · ${Number(result.distance_km).toFixed(1)} km` : ''}</span>{result.description && <p>{result.description}</p>}</a>)}</div>}</div>)}</div><form className="ask-composer" onSubmit={send}><input aria-label="Ask GeoGuide" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about this place…" /><button aria-label="Send question" className="send-button" disabled={sending} type="submit"><Send size={18} /></button></form></div>
}

function ProfileView({ interests, onReset, onLogout }) {
  return <div className="view-content"><header className="simple-header"><div><span className="eyebrow">Your preferences</span><h1>Profile</h1></div><div className="avatar">{interests.length ? interests[0][0].toUpperCase() : '?'}</div></header><div className="profile-card"><div className="profile-avatar">{interests.length ? interests[0][0].toUpperCase() : '?'}</div><h2>Local explorer</h2><p>Your recommendations adapt as your interests change.</p></div><SectionTitle eyebrow="Interests">What you care about</SectionTitle><div className="chip-row wrap">{(interests.length ? interests : ['nature', 'heritage']).map((interest) => <Chip active key={interest}>{interest}</Chip>)}</div><button className="secondary-button full-width" onClick={onReset} type="button">Run onboarding again</button><button className="logout-button" onClick={onLogout} type="button"><LogOut size={17} /> Log out</button></div>
}

function UIKitView() {
  const samplePlace = { id: 'ui-sample', name: 'Example place from API', category: 'place', distance: '1.2 km', score: 86, reasons: ['Close to your current location'] }
  return <div className="app-frame"><main className="app-scroll ui-kit"><header className="simple-header"><div><span className="eyebrow">Development route</span><h1>UI kit</h1></div><span className="status-pill">Preview</span></header><SectionTitle eyebrow="Controls">Chips and actions</SectionTitle><div className="chip-row"><Chip active>Selected</Chip><Chip>Available</Chip><IconCircleButton label="Example action"><RefreshCw size={20} /></IconCircleButton></div><SectionTitle eyebrow="Cards">Place states</SectionTitle><PlaceCard place={samplePlace} /><PlaceCard place={samplePlace} featured /><div className="skeleton-card" /><StateMessage title="Empty state" body="Unavailable values stay explicit and do not become invented content." /><button className="primary-button full-width" type="button">Primary action <ChevronRight size={18} /></button></main></div>
}

function App() {
  const [authenticated, setAuthenticated] = useState(false)
  const [onboarded, setOnboarded] = useState(() => localStorage.getItem('geoguide-onboarded') === 'true')
  const [interests, setInterests] = useState(readStoredInterests)
  const [location, setLocation] = useState(readStoredLocation)
  const [physicalLocation, setPhysicalLocation] = useState(() => { const stored = readStoredLocation(); return stored?.source === 'device' ? stored : null })
  const [destinationLocation, setDestinationLocation] = useState(() => { const stored = readStoredLocation(); return stored?.source === 'user_explicit_query' ? stored : null })
  const [permission, setPermission] = useState(() => localStorage.getItem('geoguide-location') !== 'true' || !readStoredLocation())
  const [activeTab, setActiveTab] = useState('now')
  const [selectedPlace, setSelectedPlace] = useState(null)
  const [savedPlaces, setSavedPlaces] = useState(() => { try { const value = JSON.parse(localStorage.getItem('geoguide-saved') || '[]'); return Array.isArray(value) ? value : [] } catch { return [] } })
  const [now, setNow] = useState(null)
  const [places, setPlaces] = useState([])
  const [nowPlaces, setNowPlaces] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [locationError, setLocationError] = useState('')
  const [locating, setLocating] = useState(false)
  const [ingestion, setIngestion] = useState(null)
  const [activeNearbyCategory, setActiveNearbyCategory] = useState('all')
  const [activeNearbyTime, setActiveNearbyTime] = useState('now')
  const [selectedNowCategory, setSelectedNowCategory] = useState('for_you')

  const loadData = useCallback(async () => {
    if (!location?.lat && !location?.lon && !location?.city) return
    setLoading(true)
    setError('')
    try {
      const [nowData, nowNearby] = await Promise.all([getNow(location), getNearby(location, 'now')])
      setNow(nowData)
      const normPlaces = (nowNearby.items || []).map(normalizePlace)
      setNowPlaces(normPlaces)
      setPlaces(normPlaces)
    } catch (requestError) {
      setError(requestError.message || 'The guide is temporarily unavailable.')
    } finally {
      setLoading(false)
    }
  }, [location])

  const loadNearby = useCallback(async (cat = activeNearbyCategory, time = activeNearbyTime) => {
    if (!location?.lat && !location?.lon && !location?.city) return
    try {
      const mode = time === 'now' ? 'nearby' : time === 'full' ? 'everywhere' : 'nearby'
      const categoryParam = cat === 'all' ? null : cat
      const nearbyData = await getNearby(location, mode, categoryParam)
      setPlaces((nearbyData.items || []).map(normalizePlace))
    } catch (requestError) {
      setError(requestError.message || 'Nearby places are temporarily unavailable.')
    }
  }, [location, activeNearbyCategory, activeNearbyTime])

  const handleCategoryChange = (cat) => {
    setActiveNearbyCategory(cat)
    loadNearby(cat, activeNearbyTime)
  }

  const handleTimeChange = (time) => {
    setActiveNearbyTime(time)
    loadNearby(activeNearbyCategory, time)
  }

  useEffect(() => {
    if (!onboarded || permission) return undefined
    const timer = window.setTimeout(() => { loadData() }, 0)
    return () => window.clearTimeout(timer)
  }, [onboarded, permission, loadData])

  useEffect(() => {
    if (activeTab === 'nearby' && location) {
      const timer = window.setTimeout(() => {
        loadNearby(activeNearbyCategory, activeNearbyTime)
      }, 0)
      return () => window.clearTimeout(timer)
    }
    return undefined
  }, [activeTab, location, activeNearbyCategory, activeNearbyTime, loadNearby])

  useEffect(() => {
    if (!ingestion?.job_id || ['completed', 'failed'].includes(ingestion.status)) return undefined
    const poll = () => getIngestionStatus(ingestion.job_id).then(setIngestion).catch((requestError) => setIngestion((current) => ({ ...current, status: 'failed', error: requestError.message })))
    const timer = window.setInterval(poll, 1500)
    const expiry = window.setTimeout(() => setIngestion((current) => current?.status === 'running' ? { ...current, status: 'failed', error: 'City knowledge is taking longer than expected. You can continue exploring and retry later.' } : current), 60000)
    poll()
    return () => { window.clearInterval(timer); window.clearTimeout(expiry) }
  }, [ingestion?.job_id, ingestion?.status])

  const completeOnboarding = (chosen) => {
    setInterests(chosen)
    localStorage.setItem('geoguide-interests', JSON.stringify(chosen))
    localStorage.setItem('geoguide-onboarded', 'true')
    setOnboarded(true)
    updatePreferences({ interests: Object.fromEntries(chosen.map((interest) => [interest, 1])) }).catch((requestError) => console.warn('Preference profile could not be saved:', requestError.message))
  }

  const locate = () => {
    setLocationError('')
    if (!navigator.geolocation) { setLocationError('Location is not available in this browser. Choose a destination instead.'); return }
    setLocating(true)
    navigator.geolocation.getCurrentPosition((position) => {
      const coordinates = { lat: position.coords.latitude, lon: position.coords.longitude, accuracy_meters: position.coords.accuracy, source: 'device', timestamp: new Date().toISOString(), confidence: position.coords.accuracy <= 100 ? 1 : 0.7 }
      setPhysicalLocation(coordinates)
      setDestinationLocation(null)
      setLocation(coordinates)
      setPermission(false)
      localStorage.setItem('geoguide-location', 'true')
      localStorage.setItem('geoguide-coordinates', JSON.stringify(coordinates))
      startLocationIngestion({ ...coordinates, accuracy: position.coords.accuracy }).then(setIngestion).catch((requestError) => console.warn('Location ingestion failed:', requestError.message)).finally(() => setLocating(false))
    }, (positionError) => {
      setLocating(false)
      setLocationError(positionError.message || 'Location permission was not granted. Choose a destination instead.')
    })
  }

  const chooseDestination = (destination) => {
    const nextDestination = { city: destination, source: 'user_explicit_query', timestamp: new Date().toISOString(), confidence: 0.8 }
    setDestinationLocation(nextDestination)
    setLocation(nextDestination)
    setPermission(false)
    setLocationError('')
    localStorage.setItem('geoguide-location', 'true')
    localStorage.setItem('geoguide-coordinates', JSON.stringify(nextDestination))
    startLocationIngestion(nextDestination).then((result) => { setIngestion(result); if (result.location) { setDestinationLocation({ ...result.location, source: 'user_explicit_query', timestamp: new Date().toISOString(), confidence: 0.8 }); setLocation({ ...result.location, source: 'user_explicit_query', timestamp: new Date().toISOString(), confidence: 0.8 }) } }).catch((requestError) => setLocationError(requestError.message))
  }

  const toggleSaved = (place) => {
    setSavedPlaces((current) => {
      const saved = current.includes(place.id)
      const next = saved ? current.filter((id) => id !== place.id) : [...current, place.id]
      localStorage.setItem('geoguide-saved', JSON.stringify(next))
      recordInteraction({ poi_id: place.id, category: place.category, event_type: saved ? 'unsaved' : 'saved' }).catch((requestError) => console.warn('Interaction could not be recorded:', requestError.message))
      return next
    })
  }

  const logout = async () => {
    try { await logOut() } catch (requestError) { console.warn('Logout request failed:', requestError.message) }
    finally {
      ['geoguide-session', 'geoguide-token', 'geoguide-account', 'geoguide-onboarded', 'geoguide-interests', 'geoguide-location', 'geoguide-coordinates', 'geoguide-saved'].forEach((key) => localStorage.removeItem(key))
      setAuthenticated(false)
      setOnboarded(false)
      setInterests([])
      setLocation(null)
      setPhysicalLocation(null)
      setDestinationLocation(null)
      setPermission(true)
      setSavedPlaces([])
      setNow(null)
      setPlaces([])
      setIngestion(null)
    }
  }

  if (window.location.pathname === '/dev/ui-kit') return <UIKitView />
  if (!authenticated) return <AuthView onAuthenticated={() => setAuthenticated(true)} />
  if (!onboarded) return <Onboarding onComplete={completeOnboarding} />
  if (permission) return <PermissionView onLocate={locate} onDestination={chooseDestination} busy={locating} error={locationError} />
  if (selectedPlace) return <div className="app-frame"><main className="app-scroll detail-scroll"><PlaceDetailView place={selectedPlace} saved={savedPlaces.includes(selectedPlace.id)} onSave={toggleSaved} onBack={() => setSelectedPlace(null)} now={now} /></main></div>

  // In plan view, display user-saved places, or if none saved yet, recommended stops from current places
  const planPlaces = savedPlaces.length
    ? places.filter((place) => savedPlaces.includes(place.id)).length
      ? places.filter((place) => savedPlaces.includes(place.id))
      : nowPlaces.filter((place) => savedPlaces.includes(place.id))
    : (places.length ? places : nowPlaces).slice(0, 3)

  const content = {
    now: <NowView now={now} places={nowPlaces} loading={loading} error={error} onRetry={loadData} onOpen={setSelectedPlace} onSave={toggleSaved} savedPlaces={savedPlaces} selectedCategory={selectedNowCategory} onSelectCategory={setSelectedNowCategory} ingestion={ingestion} />,
    nearby: <NearbyView places={places} savedPlaces={savedPlaces} onSave={toggleSaved} onOpen={setSelectedPlace} onRefresh={() => loadNearby(activeNearbyCategory, activeNearbyTime)} activeCategory={activeNearbyCategory} onCategoryChange={handleCategoryChange} activeTime={activeNearbyTime} onTimeChange={handleTimeChange} />,
    plan: <PlanView places={planPlaces} />,
    ask: <AskView now={now} locationContext={physicalLocation || destinationLocation || location} />,
    profile: <ProfileView interests={interests} onReset={() => { localStorage.removeItem('geoguide-onboarded'); setOnboarded(false) }} onLogout={logout} />
  }[activeTab]

  return <div className="app-frame"><main className="app-scroll">{content}</main><BottomTabBar activeTab={activeTab} onChange={setActiveTab} /></div>
}

export default App
