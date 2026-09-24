import { useEffect, useState } from 'react'
import { Check, Plus, X } from 'lucide-react'
import { getFeedbackVocabulary, submitFeedback } from '../api'
import { titleCase } from '../format'

const FACES = { 5: '😍', 4: '🙂', 3: '😐', 2: '🙁', 1: '😡' }
let vocabularyCache = null

// "How was this place?" — rating → vibes → optional likes/dislikes → optional text. Fast by design.
export default function FeedbackSheet({ place, onClose, onSaved }) {
  const [vocabulary, setVocabulary] = useState(vocabularyCache)
  const [rating, setRating] = useState(null)
  const [vibes, setVibes] = useState([])
  const [custom, setCustom] = useState([])
  const [customDraft, setCustomDraft] = useState('')
  const [showOther, setShowOther] = useState(false)
  const [liked, setLiked] = useState([])
  const [disliked, setDisliked] = useState([])
  const [details, setDetails] = useState(false)
  const [crowd, setCrowd] = useState(null)
  const [price, setPrice] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    if (vocabularyCache) return
    getFeedbackVocabulary().then((data) => { vocabularyCache = data; setVocabulary(data) }).catch((requestError) => setError(requestError.message))
  }, [])

  const toggle = (setter) => (key) => setter((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])
  const addCustom = () => {
    const value = customDraft.trim().replace(/\s+/g, ' ')
    const max = vocabulary?.custom_vibe_max_length || 30
    if (!value) return
    if (value.length > max || !/[a-zA-Z]{3}/.test(value) || /\d/.test(value)) { setError(`A custom vibe should be a word or two (under ${max} letters).`); return }
    if (custom.length >= 3 || custom.some((item) => item.toLowerCase() === value.toLowerCase())) return
    setCustom((current) => [...current, value]); setCustomDraft(''); setError('')
  }
  const submit = async () => {
    if (!rating) return
    setBusy(true); setError('')
    try {
      const saved = await submitFeedback({ place_id: place.id, place_name: place.name, category: place.category, overall_rating: rating, vibes, custom_vibes: custom, liked_aspects: liked, disliked_aspects: disliked, crowd_level: crowd, price_level: price, text_feedback: text.trim() || null, visit_date: new Date().toISOString().slice(0, 10) })
      setResult(saved)
      onSaved?.(saved)
    } catch (requestError) {
      setError(requestError.detail?.message || requestError.message)
    } finally { setBusy(false) }
  }

  return <div className="sheet-backdrop" role="dialog" aria-modal="true" aria-label={`Feedback for ${place.name}`} onClick={(event) => event.target === event.currentTarget && onClose()}>
    <section className="feedback-sheet">
      <header><div><span className="eyebrow">Your feedback</span><h2>How was {place.name}?</h2></div><button type="button" className="icon-close" aria-label="Close" onClick={onClose}><X size={18} /></button></header>
      {result ? <div className="feedback-thanks">
        <Check size={26} />
        <strong>{result.message}</strong>
        {result.community?.top_vibes?.length > 0 && <p>Visitors now describe it as {result.community.top_vibes.map((v) => v.label.toLowerCase()).join(' · ')} ({result.community.feedback_count} reviews).</p>}
        {result.your_vibes?.liked_vibes?.length > 0 && <p>You tend to enjoy: {result.your_vibes.liked_vibes.map((v) => `${v.emoji || ''} ${v.label}`).join(', ')}.</p>}
        <button type="button" className="primary-button full-width" onClick={onClose}>Done</button>
      </div> : <>
        <div className="rating-row" role="radiogroup" aria-label="Overall">
          {(vocabulary?.ratings || [5, 4, 3, 2, 1].map((value) => ({ value, label: '' }))).map((item) => <button type="button" role="radio" aria-checked={rating === item.value} key={item.value} className={`rating-face ${rating === item.value ? 'active' : ''}`} onClick={() => setRating(item.value)}><span>{FACES[item.value]}</span><small>{item.label}</small></button>)}
        </div>
        <h3>What was the vibe? <small>pick any</small></h3>
        <div className="chip-row wrap">
          {(vocabulary?.vibes || []).map((vibe) => <button type="button" key={vibe.key} className={`chip ${vibes.includes(vibe.key) ? 'active' : ''}`} aria-pressed={vibes.includes(vibe.key)} onClick={() => toggle(setVibes)(vibe.key)}>{vibe.emoji} {vibe.label}</button>)}
          {custom.map((item) => <button type="button" key={item} className="chip active" onClick={() => setCustom((current) => current.filter((c) => c !== item))}>{item} <X size={12} /></button>)}
          <button type="button" className={`chip ${showOther ? 'active' : ''}`} onClick={() => setShowOther((value) => !value)}><Plus size={13} /> Other</button>
        </div>
        {showOther && <form className="custom-vibe" onSubmit={(event) => { event.preventDefault(); addCustom() }}><input value={customDraft} maxLength={vocabulary?.custom_vibe_max_length || 30} onChange={(event) => setCustomDraft(event.target.value)} placeholder="e.g. artsy, spiritual" aria-label="Custom vibe" /><button type="submit" className="chip active">Add</button></form>}
        <button type="button" className="link-button" onClick={() => setDetails((value) => !value)}>{details ? 'Fewer details' : 'What did you like or not like? (optional)'}</button>
        {details && <>
          <h3>What did you like?</h3>
          <div className="chip-row wrap">{(vocabulary?.liked || []).map((item) => <button type="button" key={item.key} className={`chip ${liked.includes(item.key) ? 'active' : ''}`} aria-pressed={liked.includes(item.key)} onClick={() => toggle(setLiked)(item.key)}>{item.label}</button>)}</div>
          <h3>What didn't you like?</h3>
          <div className="chip-row wrap">{(vocabulary?.disliked || []).map((item) => <button type="button" key={item.key} className={`chip negative ${disliked.includes(item.key) ? 'active' : ''}`} aria-pressed={disliked.includes(item.key)} onClick={() => toggle(setDisliked)(item.key)}>{item.label}</button>)}</div>
          <h3>How busy was it?</h3>
          <div className="chip-row wrap">{(vocabulary?.crowd_levels || []).map((level) => <button type="button" key={level} className={`chip ${crowd === level ? 'active' : ''}`} onClick={() => setCrowd(crowd === level ? null : level)}>{titleCase(level)}</button>)}</div>
          <h3>Price</h3>
          <div className="chip-row wrap">{(vocabulary?.price_levels || []).map((level) => <button type="button" key={level} className={`chip ${price === level ? 'active' : ''}`} onClick={() => setPrice(price === level ? null : level)}>{titleCase(level)}</button>)}</div>
        </>}
        <textarea value={text} onChange={(event) => setText(event.target.value)} rows={2} maxLength={2000} placeholder="Tell us more (optional)" aria-label="Tell us more" />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button type="button" className="primary-button full-width" disabled={!rating || busy} onClick={submit}>{busy ? 'Saving…' : rating ? 'Send feedback' : 'Choose how it was'}</button>
      </>}
    </section>
  </div>
}
