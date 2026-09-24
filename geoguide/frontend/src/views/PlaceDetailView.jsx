import { useEffect, useState } from 'react'
import { ChevronLeft, MessageCircle, Navigation, Star } from 'lucide-react'
import { getPlace, getPlaceCommunity } from '../api'
import FeedbackSheet from '../components/FeedbackSheet'
import { AdvisoryList, ConfidenceList, ConflictNote, IconCircleButton, PlaceFacts, SourceBadge, WhyBars } from '../components/ui'
import { formatMoney, mapsLink, titleCase } from '../format'

export default function PlaceDetailView({ place: initial, context, saved, onSave, onBack, onAsk }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [community, setCommunity] = useState(null)
  const [rating, setRating] = useState(false)
  const storedPlace = !initial.id.startsWith('web-')

  useEffect(() => {
    if (!storedPlace) return
    getPlaceCommunity(initial.id).then(setCommunity).catch(() => setCommunity(null))
  }, [initial.id, storedPlace])

  useEffect(() => {
    if (!storedPlace) return
    getPlace(initial.id, context).then(setDetail).catch((requestError) => setError(requestError.message))
  }, [initial.id, context, storedPlace])

  const stored = detail?.place || {}
  // Keep what ranking computed for this traveller (reasons, bars, cost fit, travel time) over the plain stored record.
  const place = { ...initial, ...stored, reasons: initial.reasons?.length ? initial.reasons : stored.reasons, bars: initial.bars && Object.keys(initial.bars).length ? initial.bars : stored.bars, cost_for_user: initial.cost_for_user?.kind ? initial.cost_for_user : stored.cost_for_user, confidence_detail: initial.confidence_detail?.label ? initial.confidence_detail : stored.confidence_detail, conflicts: initial.conflicts?.length ? initial.conflicts : stored.conflicts, travel_min: initial.travel_min ?? stored.travel_min, detour_min: initial.detour_min, price_per_night: initial.price_per_night || stored.price_per_night, price_currency: initial.price_currency || stored.price_currency, price_source: initial.price_source || stored.price_source }
  const link = mapsLink(place)
  const foreignFee = formatMoney(place.entry_fee_foreign, place.fee_currency)
  return <div className="detail-view">
    <header className="detail-header"><IconCircleButton label="Back" onClick={onBack}><ChevronLeft size={22} /></IconCircleButton><span className="status-pill"><SourceBadge place={place} /></span></header>
    <div className="detail-art"><div className="place-art" /><div className="detail-title"><span className="category-label">{titleCase(place.category || place.kind)}{place.neighborhood ? ` · ${place.neighborhood}` : ''}</span><h1>{place.name}</h1></div></div>
    <section className="detail-sheet">
      <button className={`detail-save ${saved ? 'saved' : ''}`} aria-label={saved ? 'Remove from plan' : 'Add to plan'} onClick={() => onSave(place)} type="button">★</button>
      <PlaceFacts place={place} />
      <ConflictNote place={place} />
      {(place.reasons?.length > 0 || place.bars) && <><h2>Why this place</h2><WhyBars bars={place.bars} />{place.reasons?.length > 0 && <ul className="reason-list">{place.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}</>}
      {place.confidence_detail?.parts && <><h2>How reliable is this? <span className={`conf-${place.confidence_detail.label}`}>{titleCase(place.confidence_detail.label)}</span></h2><ConfidenceList detail={place.confidence_detail} /></>}
      {place.description && <><h2>About</h2><p>{place.description}</p></>}
      <button type="button" className="secondary-button full-width" onClick={() => setRating(true)}><Star size={16} /> How was this place?</button>
      {community?.from_feedback && <section className="community-card">
        <h2>What visitors say</h2>
        <p className="muted-text">{community.feedback_count} review{community.feedback_count === 1 ? '' : 's'}{community.rating ? ` · ${community.rating.toFixed(1)}/5` : ''} · opinions, not verified facts{community.synthetic_share ? ' · includes synthetic sample data' : ''}</p>
        {community.top_vibes?.length > 0 && <div className="chip-row wrap">{community.top_vibes.map((vibe) => <span key={vibe.key} className="chip static">{vibe.label}</span>)}</div>}
        {community.reported?.length > 0 && <p className="muted-text">Often mentioned: {community.reported.map((item) => `${item.label.toLowerCase()} (${Math.round(item.share * 100)}%)`).join(' · ')}</p>}
        {community.recent?.map((quote, index) => <blockquote key={index}>“{quote.text}”<small>{'★'.repeat(quote.rating)} · {quote.date}{quote.synthetic ? ' · sample' : ''}</small></blockquote>)}
      </section>}
      {detail?.facts?.length > 0 && <><h2>Good to know</h2><ul className="fact-list">{detail.facts.map((fact) => <li key={fact.chunk_id}>{fact.content.replace(`${place.name}: `, '')}<span className="fact-source">{fact.source}</span></li>)}</ul></>}
      <h2>Practical details</h2>
      <dl className="detail-grid">
        {place.kind === 'stay' ? <>
          <dt>Price / night</dt><dd>{place.price_per_night ? `${formatMoney(place.price_per_night, place.price_currency)} · ${place.price_source}` : 'Not available — no live rate for these dates'}</dd>
          {place.star_rating ? <><dt>Class</dt><dd>{place.star_rating}-star {place.property_type || ''}</dd></> : null}
          {place.guest_score != null && <><dt>Guest score</dt><dd>{place.guest_score.toFixed(1)}/10 from {place.review_count} reviews</dd></>}
          {place.checkin_time && <><dt>Check-in</dt><dd>{place.checkin_time} · check-out {place.checkout_time}</dd></>}
        </> : <>
          <dt>Hours</dt><dd>{place.opening_hours_text || 'Not verified'}</dd>
          <dt>Entry</dt><dd>{formatMoney(place.entry_cost ?? place.entry_fee, place.fee_currency) || 'Not verified'}{foreignFee && foreignFee !== formatMoney(place.entry_cost ?? place.entry_fee, place.fee_currency) ? ` (foreign visitors ${foreignFee})` : ''}</dd>
        </>}
        {place.cost_for_user?.note && <><dt>Your budget</dt><dd>{place.cost_for_user.fits_budget === false ? 'Over budget — ' : place.cost_for_user.fits_budget ? 'Fits — ' : ''}{place.cost_for_user.note}</dd></>}
        {place.carbon_kg != null && <><dt>Footprint</dt><dd>{place.carbon_kg} kg CO₂ per visit (dataset)</dd></>}
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
    {rating && <FeedbackSheet place={place} onClose={() => setRating(false)} onSaved={(saved) => saved.community && setCommunity((current) => ({ ...(current || {}), ...saved.community, recent: current?.recent || [] }))} />}
  </div>
}
