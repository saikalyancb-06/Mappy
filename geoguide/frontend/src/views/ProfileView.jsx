import { useState } from 'react'
import { Check, LogOut } from 'lucide-react'
import { Chip, SectionTitle } from '../components/ui'
import { titleCase } from '../format'

export default function ProfileView({ user, config, preferences, onSave, onLogout }) {
  const [draft, setDraft] = useState(preferences)
  const [status, setStatus] = useState('')
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
      await onSave(draft)
      setStatus('Saved. Recommendations and plans now use these preferences.')
    } catch (error) {
      setStatus(error.message)
    }
  }
  const option = (key, values) => <div className="chip-row wrap">{values.map((value) => <Chip key={value} active={draft[key] === value} onClick={() => setDraft((current) => ({ ...current, [key]: value }))}>{titleCase(value)}</Chip>)}</div>
  return <div className="view-content">
    <header className="simple-header"><div><span className="eyebrow">Your preferences</span><h1>Profile</h1></div><div className="avatar">{(user?.name || '?')[0].toUpperCase()}</div></header>
    <div className="profile-card"><div className="profile-avatar">{(user?.name || '?')[0].toUpperCase()}</div><h2>{user?.name || 'Traveller'}</h2><p>{user?.email}</p></div>
    <SectionTitle eyebrow="Interests">What you care about</SectionTitle>
    <div className="chip-row wrap">{(config?.interests || []).map((item) => <Chip key={item.id} active={interests.includes(item.id)} onClick={() => toggleInterest(item.id)}>{interests.includes(item.id) && <Check size={14} />}{item.label}</Chip>)}</div>
    <SectionTitle eyebrow="Budget">How much to spend</SectionTitle>
    {option('budget', config?.budgets || [])}
    <SectionTitle eyebrow="Pace">How you like to travel</SectionTitle>
    {option('pace', config?.paces || [])}
    <SectionTitle eyebrow="Walking">How much walking is OK</SectionTitle>
    {option('walking', config?.walking || [])}
    <SectionTitle eyebrow="Accessibility">Access needs</SectionTitle>
    <div className="chip-row wrap">{(config?.accessibility || []).map((item) => <Chip key={item.id} active={draft.accessibility?.includes(item.id)} onClick={() => toggleAccess(item.id)}>{item.label}</Chip>)}</div>
    <SectionTitle eyebrow="Language">Answers and briefings in</SectionTitle>
    <div className="chip-row wrap">{(config?.languages || []).map((item) => <Chip key={item.id} active={draft.language === item.id} onClick={() => setDraft((current) => ({ ...current, language: item.id }))}>{item.label}</Chip>)}</div>
    <button className="primary-button full-width" onClick={save} type="button">Save preferences</button>
    {status && <p className="muted-text">{status}</p>}
    <button className="logout-button" onClick={onLogout} type="button"><LogOut size={17} /> Log out</button>
  </div>
}
