import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, ChevronRight } from 'lucide-react'
import { describeLocation, getConfig, getCurrentUser, getHealth, getPreferences, getPrefetchStatus, getToken, logIn, logOut, prefetchArea, recordInteraction, signUp, updatePreferences } from './api'
import ContextBar from './components/ContextBar'
import { BottomTabBar, Chip, StateMessage } from './components/ui'
import { useDeviceLocation } from './hooks/useDeviceLocation'
import AskView from './views/AskView'
import NearbyView from './views/NearbyView'
import ExploreView from './views/ExploreView'
import PlaceDetailView from './views/PlaceDetailView'
import PlanView from './views/PlanView'
import ProfileView from './views/ProfileView'
import StartView from './views/StartView'
import './App.css'

const DESTINATION_KEY = 'geoguide-destination'
const SAVED_KEY = 'geoguide-saved'
const MOVE_THRESHOLD_M = 150
const REFRESH_AFTER_MS = 5 * 60 * 1000
const DEFAULT_PREFERENCES = { interests: {}, budget: 'moderate', pace: 'balanced', walking: 'moderate', accessibility: [], language: 'en', max_daily_budget: null, budget_currency: 'INR', travel_mode: null }

const readJson = (key, fallback) => {
  try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback } catch { return fallback }
}
const writeJson = (key, value) => {
  try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(value)) } catch { /* storage unavailable */ }
}

const metersBetween = (a, b) => {
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLon = (b.lon - a.lon) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2
  return 2 * 6371000 * Math.asin(Math.sqrt(h))
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
      onAuthenticated(result.user)
    } catch (requestError) {
      setError(requestError.message || 'Authentication failed.')
    } finally { setBusy(false) }
  }
  return <div className="auth-screen"><div className="auth-brand"><span className="brand-mark">G</span><span className="eyebrow">Your place companion</span></div><div className="auth-copy"><h1>{mode === 'signup' ? 'Make every place feel closer.' : 'Welcome back.'}</h1><p>{mode === 'signup' ? 'Create your GeoGuide account to keep preferences and plans with you.' : 'Sign in to continue your local guide.'}</p></div><div className="auth-tabs"><button className={mode === 'signup' ? 'active' : ''} onClick={() => { setMode('signup'); setError('') }} type="button">Sign up</button><button className={mode === 'login' ? 'active' : ''} onClick={() => { setMode('login'); setError('') }} type="button">Log in</button></div><form className="auth-form" onSubmit={submit}>{mode === 'signup' && <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Your name" autoComplete="name" /></label>}<label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" autoComplete="email" required /></label><label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 6 characters" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="primary-button" disabled={busy} type="submit">{busy ? 'Connecting…' : mode === 'signup' ? 'Create account' : 'Log in'} <ChevronRight size={18} /></button></form><p className="auth-note">Your account is stored by your GeoGuide backend.</p></div>
}

function Onboarding({ config, onComplete }) {
  const [selected, setSelected] = useState([])
  if (!config) return <div className="loading-screen">Loading…</div>
  return <div className="onboarding-screen">
    <div className="progress-dots"><span className="active" /><span /><span /></div>
    <div className="onboarding-copy"><span className="brand-mark">G</span><span className="eyebrow">Your place companion</span><h1>What makes a place feel right?</h1><p>Choose a few interests. GeoGuide uses them to rank places around your time and location.</p></div>
    <div className="option-grid">{config.interests.map((option) => <Chip key={option.id} active={selected.includes(option.id)} onClick={() => setSelected((current) => current.includes(option.id) ? current.filter((item) => item !== option.id) : [...current, option.id])}>{selected.includes(option.id) && <Check size={16} />}{option.label}</Chip>)}</div>
    <button className="primary-button onboarding-continue" disabled={!selected.length} onClick={() => onComplete(selected)} type="button">Continue <ChevronRight size={18} /></button>
    <button className="skip-button" onClick={() => onComplete([])} type="button">Skip for now</button>
  </div>
}

function App() {
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState('')
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES)
  const [onboarded, setOnboarded] = useState(() => readJson('geoguide-onboarded', false))
  const [destination, setDestination] = useState(() => readJson(DESTINATION_KEY, null))
  const [choosingStart, setChoosingStart] = useState(false)
  const [activeTab, setActiveTab] = useState('now')
  const [selectedPlace, setSelectedPlace] = useState(null)
  const [askPlace, setAskPlace] = useState(null)
  const [savedIds, setSavedIds] = useState(() => readJson(SAVED_KEY, []))
  const [selectedDate, setSelectedDate] = useState(null) // null = the city's today; set by the Explore date picker
  const [prefetch, setPrefetch] = useState(null)
  const [debugAvailable, setDebugAvailable] = useState(false)
  const device = useDeviceLocation()
  const [stableLocation, setStableLocation] = useState(device.location)

  // Session + configuration
  useEffect(() => {
    getConfig().then(setConfig).catch((error) => setConfigError(error.message))
    getHealth().then((health) => setDebugAvailable(health.environment !== 'production')).catch(() => {})
    if (!getToken()) { window.setTimeout(() => setAuthChecked(true), 0); return }
    getCurrentUser().then(({ user: current }) => setUser(current)).catch(() => localStorage.removeItem('geoguide-token')).finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!user) return
    getPreferences().then((stored) => setPreferences({ ...DEFAULT_PREFERENCES, ...stored })).catch(() => {})
  }, [user])

  // Only propagate meaningful movement so screens don't reload on every GPS tick.
  useEffect(() => {
    const next = device.location
    const timer = window.setTimeout(() => setStableLocation((current) => {
      if (!next || !current) return next
      if (metersBetween(current, next) > MOVE_THRESHOLD_M) return next
      if (Date.parse(next.timestamp) - Date.parse(current.timestamp) > REFRESH_AFTER_MS) return next
      return current
    }), 0)
    return () => window.clearTimeout(timer)
  }, [device.location])

  const context = useMemo(() => ({ userLocation: stableLocation, destination }), [stableLocation, destination])

  // Which city the device is in (shown in the context bar; lets the traveller switch to it).
  const [hereState, setHereState] = useState({ location: null, city: null })
  useEffect(() => {
    if (!stableLocation) return undefined
    let cancelled = false
    describeLocation(stableLocation)
      .then((result) => { if (!cancelled) setHereState({ location: stableLocation, city: result.city || null }) })
      .catch(() => { if (!cancelled) setHereState({ location: stableLocation, city: null }) })
    return () => { cancelled = true }
  }, [stableLocation])
  const here = stableLocation && hereState.location === stableLocation ? hereState.city : null

  // Warm stores for a non-curated destination (OpenStreetMap + weather) and show progress.
  useEffect(() => {
    if (!prefetch?.job_id || ['completed', 'failed'].includes(prefetch.status)) return undefined
    const timer = window.setInterval(() => getPrefetchStatus(prefetch.job_id).then(setPrefetch).catch(() => setPrefetch((current) => ({ ...current, status: 'failed' }))), 1500)
    return () => window.clearInterval(timer)
  }, [prefetch])

  const chooseDestination = useCallback((place) => {
    const next = { name: place.name, destination_id: place.destination_id || null, lat: place.lat, lon: place.lon, region: place.region, country: place.country, coverage_radius_km: place.coverage_radius_km, kind: place.kind }
    setDestination(next)
    writeJson(DESTINATION_KEY, next)
    setChoosingStart(false)
    setSelectedPlace(null)
    if (!next.destination_id) prefetchArea({ destination: next }).then(setPrefetch).catch(() => setPrefetch(null))
  }, [])

  const clearDestination = () => { setDestination(null); writeJson(DESTINATION_KEY, null) }

  // "Use my current location" on the start screen: explore the city the device is in.
  // A stored city becomes the destination; anywhere else, GeoGuide works from the GPS point itself.
  const [wantHere, setWantHere] = useState(false)
  const useMyLocation = () => { setWantHere(true); if (!device.location) device.start() }
  useEffect(() => {
    if (!wantHere || !stableLocation || hereState.location !== stableLocation) return undefined
    const timer = window.setTimeout(() => {
      setWantHere(false)
      if (hereState.city?.destination_id) chooseDestination(hereState.city)
      else { setDestination(null); writeJson(DESTINATION_KEY, null); setChoosingStart(false) }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [wantHere, stableLocation, hereState, chooseDestination])
  useEffect(() => {
    if (!wantHere || !['denied', 'unavailable', 'unsupported'].includes(device.status)) return undefined
    const timer = window.setTimeout(() => setWantHere(false), 0)
    return () => window.clearTimeout(timer)
  }, [wantHere, device.status])

  const completeOnboarding = (chosen) => {
    const interests = Object.fromEntries(chosen.map((interest) => [interest, 1]))
    setPreferences((current) => ({ ...current, interests }))
    writeJson('geoguide-onboarded', true)
    setOnboarded(true)
    updatePreferences({ interests }).catch(() => {})
  }

  const savePreferences = async (draft) => {
    const saved = await updatePreferences(draft)
    setPreferences({ ...DEFAULT_PREFERENCES, ...saved })
  }

  const toggleSaved = (place) => {
    setSavedIds((current) => {
      const saved = current.includes(place.id)
      const next = saved ? current.filter((id) => id !== place.id) : [...current, place.id]
      writeJson(SAVED_KEY, next)
      recordInteraction({ poi_id: place.id, category: place.category, event_type: saved ? 'unsaved' : 'saved' }).catch(() => {})
      return next
    })
  }

  const logout = async () => {
    try { await logOut() } catch { /* already logged out */ }
    ;['geoguide-token', 'geoguide-onboarded', DESTINATION_KEY, SAVED_KEY].forEach((key) => localStorage.removeItem(key))
    device.stop()
    setUser(null)
    setOnboarded(false)
    setDestination(null)
    setSavedIds([])
    setPreferences(DEFAULT_PREFERENCES)
  }

  if (window.location.pathname === '/dev/ui-kit') return <StateMessage title="UI kit moved" body="Components live in src/components." />
  if (!authChecked) return <div className="loading-screen">Loading GeoGuide…</div>
  if (configError) return <div className="loading-screen"><StateMessage title="Backend unavailable" body={configError} action={<button className="secondary-button" onClick={() => window.location.reload()} type="button">Retry</button>} /></div>
  if (!user) return <AuthView onAuthenticated={setUser} />
  if (!onboarded) return <Onboarding config={config} onComplete={completeOnboarding} />
  if (choosingStart || (!destination && !stableLocation)) {
    return <StartView device={device} here={here} locating={wantHere} onUseLocation={useMyLocation} onChooseDestination={chooseDestination} canContinue={Boolean(destination || stableLocation)} onContinue={() => setChoosingStart(false)} onCancel={destination || stableLocation ? () => setChoosingStart(false) : null} />
  }

  const openPlace = (place) => setSelectedPlace(place)
  const views = {
    now: <ExploreView context={context} language={preferences.language} selectedDate={selectedDate} onDateChange={setSelectedDate} onOpen={openPlace} onSave={toggleSaved} savedIds={savedIds} onAsk={() => setActiveTab('ask')} onGoNearby={() => setActiveTab('nearby')} onChooseDestination={chooseDestination} />,
    nearby: <NearbyView context={context} config={config} onOpen={openPlace} onSave={toggleSaved} savedIds={savedIds} onEnableLocation={device.start} onChooseDestination={chooseDestination} hasBudget={Boolean(preferences.max_daily_budget)} />,
    plan: <PlanView context={context} config={config} savedIds={savedIds} onToggleSaved={toggleSaved} onOpen={openPlace} onEnableLocation={device.start} profile={preferences} />,
    ask: <AskView context={context} selectedDate={selectedDate} onClearDate={() => setSelectedDate(null)} language={preferences.language} selectedPlace={askPlace} onClearSelected={() => setAskPlace(null)} onAdoptDestination={chooseDestination} onOpen={openPlace} debugAvailable={debugAvailable} />,
    profile: <ProfileView key={JSON.stringify(preferences)} user={user} config={config} preferences={preferences} onSave={savePreferences} onLogout={logout} />,
  }

  return <div className="app-frame">
    <main className={`app-scroll ${selectedPlace ? 'detail-scroll' : ''}`}>
      {!selectedPlace && <ContextBar destination={destination} device={device} here={stableLocation ? here : null} onExploreHere={() => here?.destination_id && chooseDestination(here)} onChangeDestination={() => setChoosingStart(true)} onClearDestination={clearDestination} onEnableLocation={device.start} />}
      {prefetch && prefetch.status !== 'completed' && !selectedPlace && <section className="ingestion-card"><div className="verified-row"><span className="verified-dot" /> Preparing {destination?.name} <strong>{prefetch.progress || 0}%</strong></div><div className="progress-track"><span style={{ width: `${prefetch.progress || 0}%` }} /></div><p>{prefetch.status === 'failed' ? `Some sources could not be loaded${prefetch.error ? ` (${prefetch.error})` : ''}. You can keep exploring with what is available.` : 'Collecting places and live conditions for this area.'}</p></section>}
      {selectedPlace
        ? <PlaceDetailView place={selectedPlace} context={context} saved={savedIds.includes(selectedPlace.id)} onSave={toggleSaved} onBack={() => setSelectedPlace(null)} onAsk={(place) => { setAskPlace(place); setSelectedPlace(null); setActiveTab('ask') }} />
        : views[activeTab]}
    </main>
    {!selectedPlace && <BottomTabBar activeTab={activeTab} onChange={setActiveTab} />}
  </div>
}

export default App
