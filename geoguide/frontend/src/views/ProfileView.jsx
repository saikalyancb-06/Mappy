import { useEffect, useState } from 'react'
import { Check, LogOut } from 'lucide-react'
import { getMyVibes } from '../api'
import OfflineCities from '../components/OfflineCities'
import SavedPlaces from '../components/SavedPlaces'
import SubmissionsPanel from '../components/SubmissionsPanel'
import { Chip, SectionTitle } from '../components/ui'
import { titleCase } from '../format'

export default function ProfileView({ user, config, preferences, onSave, onLogout, savedIds = [], onToggleSaved, onOpen }) {
  const [draft, setDraft] = useState(preferences)
  const [status, setStatus] = useState('')
  const [vibes, setVibes] = useState(null)
  useEffect(() => { getMyVibes().then(setVibes).catch(() => setVibes(null)) }, [])
  const interests = Object.keys(draft.interests || {})
  const toggleInterest = (id) => setDraft((current) => {
    const next = { ...(current.interests || {}) }
    if (next[id]) delete next[id]
    else next[id] = 1
    return { ...current, interests: next }
  })
  const toggleAccess = (id) => setDraft((current) => ({ ...current, accessibility: current.accessibility?.includes(id) ? current.accessibility.filter((item) => item !== id) : [...(current.accessibility || []), id] }))
  const save = async () => {
    setStatus('Saving…')
    try {
      await onSave({ ...draft, max_daily_budget: draft.max_daily_budget === '' ? null : draft.max_daily_budget, budget_currency: draft.budget_currency || 'INR' })
      setStatus('Saved. Recommendations and plans now use these preferences.')
    } catch (error) {
      setStatus(error.message)
    }
  }
  const option = (key, values) => <div className="chip-row wrap">{values.map((value) => <Chip key={value} active={draft[key] === value} onClick={() => setDraft((current) => ({ ...current, [key]: value }))}>{titleCase(value)}</Chip>)}</div>
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">Your preferences</span><h1>Profile</h1></div><div className="avatar">{(user?.name || '?')[0].toUpperCase()}</div></header>
    <div className="profile-card"><div className="profile-avatar">{(user?.name || '?')[0].toUpperCase()}</div><h2>{user?.name || 'Traveller'}</h2><p>{user?.email}</p></div>
    <SectionTitle eyebrow="Learned from your feedback">Your vibe profile</SectionTitle>
    {vibes && (vibes.status === 'cold_start'
      ? <p className="muted-text">Rate a few places you visit ("How was this place?") and GeoGuide will learn the vibes you enjoy.</p>
      : <div className="vibe-profile">
        {vibes.liked_vibes.length > 0 && <p><strong>You enjoy</strong></p>}
        <div className="chip-row wrap">{vibes.liked_vibes.map((vibe) => <span key={vibe.key} className="chip static active">{vibe.emoji} {vibe.label}</span>)}</div>
        {vibes.disliked_vibes.length > 0 && <><p><strong>Less your thing</strong></p><div className="chip-row wrap">{vibes.disliked_vibes.map((vibe) => <span key={vibe.key} className="chip static">{vibe.emoji} {vibe.label}</span>)}</div></>}
        {vibes.avoids.length > 0 && <p className="muted-text">You've flagged: {vibes.avoids.map((item) => item.label.toLowerCase()).join(', ')}</p>}
        <p className="muted-text">Based on {vibes.feedback_count} place{vibes.feedback_count === 1 ? '' : 's'} you rated{vibes.status === 'emerging' ? ' — still learning' : ''}.</p>
      </div>)}
    {onToggleSaved && <SavedPlaces savedIds={savedIds} onToggleSaved={onToggleSaved} onOpen={onOpen} />}
    <OfflineCities />
    <SubmissionsPanel />
    <SectionTitle eyebrow="Interests">What you care about</SectionTitle>
    <div className="chip-row wrap">{(config?.interests || []).map((item) => <Chip key={item.id} active={interests.includes(item.id)} onClick={() => toggleInterest(item.id)}>{interests.includes(item.id) && <Check size={14} />}{item.label}</Chip>)}</div>
    <SectionTitle eyebrow="Budget">How much to spend</SectionTitle>
    {option('budget', config?.budgets || [])}
    <SectionTitle eyebrow="Pace">How you like to travel</SectionTitle>
    {option('pace', config?.paces || [])}
    <SectionTitle eyebrow="Walking">How much walking is OK</SectionTitle>
    {option('walking', config?.walking || [])}
    <SectionTitle eyebrow="Budget">Daily spending limit</SectionTitle>
    <div className="budget-row">
      <select aria-label="Budget currency" value={draft.budget_currency || 'INR'} onChange={(event) => setDraft((current) => ({ ...current, budget_currency: event.target.value }))}>{(config?.currencies || ['INR']).map((code) => <option key={code} value={code}>{code}</option>)}</select>
      <input inputMode="decimal" aria-label="Daily budget" placeholder="e.g. 2500" value={draft.max_daily_budget ?? ''} onChange={(event) => setDraft((current) => ({ ...current, max_daily_budget: event.target.value.replace(/[^\d.]/g, '') }))} />
    </div>
    <p className="muted-text">Every place shows its cost against this, and plans stay within it.</p>
    <SectionTitle eyebrow="Getting around">Usual transport</SectionTitle>
    <div className="chip-row wrap">{(config?.travel_modes || []).map((item) => <Chip key={item.id} active={draft.travel_mode === item.id} onClick={() => setDraft((current) => ({ ...current, travel_mode: current.travel_mode === item.id ? null : item.id }))}>{item.label}</Chip>)}</div>
    <SectionTitle eyebrow="Accessibility">Access needs</SectionTitle>
    <div className="chip-row wrap">{(config?.accessibility || []).map((item) => <Chip key={item.id} active={draft.accessibility?.includes(item.id)} onClick={() => toggleAccess(item.id)}>{item.label}</Chip>)}</div>
    <SectionTitle eyebrow="Suggestions">What to leave out</SectionTitle>
    <div className="chip-row wrap"><Chip active={Boolean(draft.exclude_places_of_worship)} onClick={() => setDraft((current) => ({ ...current, exclude_places_of_worship: !current.exclude_places_of_worship }))}>{draft.exclude_places_of_worship && <Check size={14} />}Leave places of worship out of suggestions</Chip></div>
    <p className="muted-text">Applies to all faiths alike. You can still ask about any place by name.</p>
    <SectionTitle eyebrow="Language">Answers and briefings in</SectionTitle>
    <div className="chip-row wrap">{(config?.languages || []).map((item) => <Chip key={item.id} active={draft.language === item.id} onClick={() => setDraft((current) => ({ ...current, language: item.id }))}>{item.label}</Chip>)}</div>
    <button className="primary-button full-width" onClick={save} type="button">Save preferences</button>
    {status && <p className="muted-text">{status}</p>}
    <button className="logout-button" onClick={onLogout} type="button"><LogOut size={17} /> Log out</button>
  </div>
}
