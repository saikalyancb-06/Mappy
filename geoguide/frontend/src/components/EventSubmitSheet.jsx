import { useState } from 'react'
import { Check, X } from 'lucide-react'
import { submitEvent } from '../api'

const CATEGORIES = [
  ['festivals', 'Festival'], ['religious', 'Temple / religious'], ['music', 'Music'], ['cultural', 'Culture & dance'], ['theatre', 'Theatre'],
  ['comedy', 'Comedy'], ['art', 'Art'], ['exhibition', 'Exhibition'], ['food', 'Food'], ['markets', 'Market / mela'], ['workshops', 'Workshop'],
  ['education', 'Talk / literature'], ['sports', 'Sports'], ['family', 'Family & kids'], ['community', 'Community'], ['outdoor', 'Outdoor'], ['nightlife', 'Nightlife'], ['other', 'Other'],
]
const ROLES = [['organiser', "I'm organising it"], ['venue', "It's at my venue"], ['attendee', "I'm going / I know about it"]]
const PRICES = [['free', 'Free'], ['paid', 'Paid'], ['donation', 'Donation'], ['unknown', 'Not sure']]

// "Add an event": organisers, venues and attendees send events no API lists. Nothing is shown until a moderator approves it.
export default function EventSubmitSheet({ city, onClose }) {
  const [form, setForm] = useState({ role: 'organiser', category: 'festivals', price_kind: 'free', currency: 'INR' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)
  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }))

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true); setError(null)
    try {
      const result = await submitEvent({ ...form, destination_id: city.destination_id })
      setDone(result.message)
    } catch (requestError) {
      setError(requestError.message || 'Could not send this event.')
    } finally { setBusy(false) }
  }

  return <div className="sheet-backdrop" role="dialog" aria-modal="true" aria-label={`Add an event in ${city.name}`} onClick={(event) => event.target === event.currentTarget && onClose()}>
    <form className="feedback-sheet event-submit-sheet" onSubmit={submit}>
      <header><div><span className="eyebrow">Add an event</span><h2>What's happening in {city.name}?</h2></div><button type="button" className="context-clear" aria-label="Close" onClick={onClose}><X size={16} /></button></header>
      {done
        ? <div className="feedback-thanks"><Check size={22} /><p>{done}</p><button type="button" className="primary-button" onClick={onClose}>Done</button></div>
        : <>
          <p className="muted-text">A moderator checks every event before it appears. Your email is only used to confirm details and is never shown.</p>
          <label>Your connection<select value={form.role} onChange={set('role')}>{ROLES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          <label>Event name<input required minLength={3} maxLength={120} value={form.title || ''} onChange={set('title')} placeholder="e.g. Kadalekai Parishe" /></label>
          <label>Category<select value={form.category} onChange={set('category')}>{CATEGORIES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          <div className="form-row">
            <label>Starts<input type="date" required value={form.start_date || ''} onChange={set('start_date')} /></label>
            <label>Ends <small>(optional)</small><input type="date" value={form.end_date || ''} min={form.start_date} onChange={set('end_date')} /></label>
          </div>
          <div className="form-row">
            <label>From <small>(optional)</small><input type="time" value={form.start_time || ''} onChange={set('start_time')} /></label>
            <label>To <small>(optional)</small><input type="time" value={form.end_time || ''} onChange={set('end_time')} /></label>
          </div>
          <label>Venue<input required minLength={2} maxLength={160} value={form.venue_name || ''} onChange={set('venue_name')} placeholder="e.g. Dodda Ganesha Temple, Basavanagudi" /></label>
          <label>Address <small>(optional)</small><input maxLength={300} value={form.venue_address || ''} onChange={set('venue_address')} /></label>
          <label>Entry<select value={form.price_kind} onChange={set('price_kind')}>{PRICES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          {form.price_kind === 'paid' && <div className="form-row">
            <label>Lowest price<input required inputMode="decimal" value={form.price_min || ''} onChange={set('price_min')} placeholder="500" /></label>
            <label>Currency<input required maxLength={3} value={form.currency || ''} onChange={set('currency')} /></label>
          </div>}
          <label>Tickets or event page <small>(optional)</small><input type="url" value={form.ticket_url || ''} onChange={set('ticket_url')} placeholder="https://" /></label>
          <label>About it <small>(optional)</small><textarea rows={3} maxLength={2000} value={form.description || ''} onChange={set('description')} /></label>
          <label>Organiser or group name<input required minLength={2} maxLength={120} value={form.organiser_name || ''} onChange={set('organiser_name')} /></label>
          <label>Your email<input type="email" required value={form.contact_email || ''} onChange={set('contact_email')} autoComplete="email" /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-button" disabled={busy} type="submit">{busy ? 'Sending…' : 'Send for review'}</button>
        </>}
    </form>
  </div>
}
