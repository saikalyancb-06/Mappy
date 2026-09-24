import { useEffect, useState } from 'react'
import { ChevronLeft, MessageCircle, Navigation } from 'lucide-react'
import { getPlace } from '../api'
import { AdvisoryList, IconCircleButton, PlaceFacts, SourceBadge } from '../components/ui'
import { formatMoney, mapsLink, titleCase } from '../format'

export default function PlaceDetailView({ place: initial, context, saved, onSave, onBack, onAsk }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const storedPlace = !initial.id.startsWith('web-')

  useEffect(() => {
    if (!storedPlace) return
    getPlace(initial.id, context).then(setDetail).catch((requestError) => setError(requestError.message))
  }, [initial.id, context, storedPlace])

  const place = { ...initial, ...(detail?.place || {}), reasons: initial.reasons?.length ? initial.reasons : detail?.place?.reasons }
  const link = mapsLink(place)
  const foreignFee = formatMoney(place.entry_fee_foreign, place.fee_currency)
  return <div className="detail-view">
    <header className="detail-header"><IconCircleButton label="Back" onClick={onBack}><ChevronLeft size={22} /></IconCircleButton><span className="status-pill"><SourceBadge place={place} /></span></header>
    <div className="detail-art"><div className="place-art" /><div className="detail-title"><span className="category-label">{titleCase(place.category || place.kind)}{place.neighborhood ? ` · ${place.neighborhood}` : ''}</span><h1>{place.name}</h1></div></div>
    <section className="detail-sheet">
      <button className={`detail-save ${saved ? 'saved' : ''}`} aria-label={saved ? 'Remove from plan' : 'Add to plan'} onClick={() => onSave(place)} type="button">★</button>
      <PlaceFacts place={place} />
      {place.reasons?.length > 0 && <><h2>Why this suits you</h2><ul className="reason-list">{place.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></>}
      {place.description && <><h2>About</h2><p>{place.description}</p></>}
      {detail?.facts?.length > 0 && <><h2>Good to know</h2><ul className="fact-list">{detail.facts.map((fact) => <li key={fact.chunk_id}>{fact.content.replace(`${place.name}: `, '')}<span className="fact-source">{fact.source}</span></li>)}</ul></>}
      <h2>Practical details</h2>
      <dl className="detail-grid">
        <dt>Hours</dt><dd>{place.opening_hours_text || 'Not verified'}</dd>
        <dt>Entry</dt><dd>{formatMoney(place.entry_fee, place.fee_currency) || 'Not verified'}{foreignFee && foreignFee !== formatMoney(place.entry_fee, place.fee_currency) ? ` (foreign visitors ${foreignFee})` : ''}</dd>
        {place.fee_notes && <><dt>Fee notes</dt><dd>{place.fee_notes}</dd></>}
        <dt>Access</dt><dd>{place.step_free == null ? 'Step-free access not verified' : place.step_free ? 'Step-free' : 'Not step-free'}{place.accessibility_notes ? ` — ${place.accessibility_notes}` : ''}</dd>
        {place.walking_effort && <><dt>Walking</dt><dd>{titleCase(place.walking_effort)} effort</dd></>}
        {place.best_time && <><dt>Best time</dt><dd>{place.best_time}</dd></>}
        {place.address && <><dt>Address</dt><dd>{place.address}</dd></>}
        {place.phone && <><dt>Phone</dt><dd>{place.phone}</dd></>}
        {place.coordinate_precision === 'approximate' && <><dt>Map pin</dt><dd>Approximate location</dd></>}
      </dl>
      {detail?.advisories?.length > 0 && <><h2>Safety</h2><AdvisoryList advisories={detail.advisories} /></>}
      {error && <p className="form-error">{error}</p>}
      <div className="source-list">{(place.sources || []).map((source) => <span key={`${source.source_type}-${source.source}`}>{source.source}{source.retrieved_at ? ` · ${source.retrieved_at.slice(0, 10)}` : ''}</span>)}</div>
      <div className="detail-actions">
        <button className="secondary-button" onClick={() => onAsk(place)} type="button"><MessageCircle size={17} /> Ask about it</button>
        {link ? <a className="primary-button" href={link} target="_blank" rel="noopener noreferrer"><Navigation size={17} /> Navigate</a> : <button className="primary-button" disabled type="button">No map location</button>}
      </div>
    </section>
  </div>
}
