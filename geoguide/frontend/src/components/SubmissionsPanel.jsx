import { useCallback, useEffect, useState } from 'react'
import { getMySubmissions, getReviewQueue, reviewSubmission } from '../api'
import { formatDay } from '../format'
import { SectionTitle } from './ui'

const STATUS = { pending: 'Waiting for review', approved: 'Published', rejected: 'Not published' }
const when = (item) => `${formatDay(item.start_date)}${item.end_date && item.end_date !== item.start_date ? ` – ${formatDay(item.end_date)}` : ''}${item.start_time ? ` · ${item.start_time}` : ''}`

function ReviewItem({ item, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const decide = async (decision) => {
    setBusy(true); setError('')
    try { await reviewSubmission(item.id, decision, note); onDone() } catch (requestError) { setError(requestError.message) } finally { setBusy(false) }
  }
  return <article className="submission-card">
    <span className="category-label">{item.category} · {item.city || item.destination_id} · {item.role}</span>
    <h3>{item.title}</h3>
    <p className="event-meta">{when(item)} · {item.venue_name}{item.venue_address ? `, ${item.venue_address}` : ''}</p>
    {item.description && <p>{item.description}</p>}
    <p className="muted-text">{item.organiser_name} · {item.contact_email} · {item.price_kind}{item.price_min ? ` from ${item.price_min} ${item.currency}` : ''}</p>
    {(item.ticket_url || item.source_url) && <a className="link-button" href={item.ticket_url || item.source_url} target="_blank" rel="noopener noreferrer">Check the link</a>}
    <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Note for the submitter (required to reject)" />
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="event-actions"><button type="button" className="primary-button small" disabled={busy} onClick={() => decide('approve')}>Approve & publish</button><button type="button" className="secondary-button small" disabled={busy} onClick={() => decide('reject')}>Reject</button></div>
  </article>
}

// Events the traveller sent in (with review status), and the review queue for moderators.
export default function SubmissionsPanel() {
  const [mine, setMine] = useState(null)
  const [queue, setQueue] = useState([])
  const load = useCallback(() => {
    getMySubmissions().then((result) => {
      setMine(result)
      if (result.moderator) getReviewQueue().then((q) => setQueue(q.items)).catch(() => setQueue([]))
    }).catch(() => setMine(null))
  }, [])
  useEffect(() => { const t = window.setTimeout(load, 0); return () => window.clearTimeout(t) }, [load])
  if (!mine || (!mine.items.length && !mine.moderator)) return null
  return <>
    {mine.items.length > 0 && <>
      <SectionTitle eyebrow="Events you added">Your submissions</SectionTitle>
      <div className="submission-list">{mine.items.map((item) => <article key={item.id} className={`submission-card status-${item.status}`}>
        <span className="category-label">{STATUS[item.status] || item.status}</span>
        <h3>{item.title}</h3>
        <p className="event-meta">{when(item)} · {item.venue_name}{item.city ? ` · ${item.city}` : ''}</p>
        {item.review_note && <p className="muted-text">Moderator: {item.review_note}</p>}
      </article>)}</div>
    </>}
    {mine.moderator && <>
      <SectionTitle eyebrow="Moderator">Events waiting for review · {queue.length}</SectionTitle>
      {queue.length ? <div className="submission-list">{queue.map((item) => <ReviewItem key={item.id} item={item} onDone={load} />)}</div> : <p className="muted-text">Nothing to review right now.</p>}
    </>}
  </>
}
