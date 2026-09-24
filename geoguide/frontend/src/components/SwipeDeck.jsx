import { useEffect, useRef, useState } from 'react'
import { Check, Heart, RotateCcw, X } from 'lucide-react'
import { ConfidenceBadge, PlaceFacts } from './ui'
import { titleCase } from '../format'

const THRESHOLD = 110 // px of drag that counts as a decision
const ART = { temple: 'heritage', monument: 'heritage', museum: 'heritage', viewpoint: 'nature', lake: 'water', beach: 'water', park: 'nature', wildlife: 'nature', market: 'city', cafe: 'food', restaurant: 'food' }

// Tinder-style deck: swipe (or drag) right to go, left to skip. Buttons and arrow keys do the same.
export default function SwipeDeck({ cards, decisions, onDecide, onUndo, onOpen }) {
  const [drag, setDrag] = useState({ x: 0, y: 0, active: false })
  const [leaving, setLeaving] = useState(null) // 'like' | 'pass' while the top card flies out
  const start = useRef(null)
  const deckRef = useRef(null)
  const decided = new Set(Object.keys(decisions))
  const remaining = cards.filter((card) => !decided.has(card.id))
  const top = remaining[0]

  const decide = (verdict) => {
    if (!top || leaving) return
    setLeaving(verdict)
    window.setTimeout(() => { onDecide(top, verdict); setLeaving(null); setDrag({ x: 0, y: 0, active: false }) }, 220)
  }

  useEffect(() => {
    const node = deckRef.current
    if (!node) return undefined
    const onKey = (event) => {
      if (event.key === 'ArrowRight') decide('like')
      else if (event.key === 'ArrowLeft') decide('pass')
      else if (event.key === 'Backspace' || (event.key === 'z' && (event.metaKey || event.ctrlKey))) onUndo()
    }
    node.addEventListener('keydown', onKey)
    return () => node.removeEventListener('keydown', onKey)
  })

  const onPointerDown = (event) => {
    if (event.target.closest('button')) return
    start.current = { x: event.clientX, y: event.clientY }
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrag({ x: 0, y: 0, active: true })
  }
  const onPointerMove = (event) => {
    if (!start.current) return
    setDrag({ x: event.clientX - start.current.x, y: (event.clientY - start.current.y) * 0.3, active: true })
  }
  const onPointerUp = () => {
    if (!start.current) return
    start.current = null
    if (drag.x > THRESHOLD) decide('like')
    else if (drag.x < -THRESHOLD) decide('pass')
    else setDrag({ x: 0, y: 0, active: false })
  }

  const liked = cards.filter((card) => decisions[card.id] === 'like')
  const passed = cards.filter((card) => decisions[card.id] === 'pass')
  const offset = leaving === 'like' ? 520 : leaving === 'pass' ? -520 : drag.x
  const hint = offset > 30 ? 'like' : offset < -30 ? 'pass' : null

  return <section className="swipe-deck" ref={deckRef} tabIndex={0} aria-label="Swipe cards: right arrow to go, left arrow to skip">
    <div className="swipe-status"><span>{Math.min(cards.length - remaining.length + 1, cards.length)} of {cards.length}</span><span className="swipe-tally"><Heart size={13} /> {liked.length} · <X size={13} /> {passed.length}</span></div>
    <div className="swipe-stack">
      {remaining.length === 0 && <div className="swipe-empty"><Check size={26} /><strong>All cards decided</strong><p>{liked.length ? `${liked.length} place${liked.length > 1 ? 's' : ''} you want to visit will be kept in the plan.` : 'You skipped every card; the plan will use other places.'}</p></div>}
      {remaining.slice(0, 3).reverse().map((card) => {
        const isTop = card.id === top.id
        const depth = remaining.indexOf(card)
        const style = isTop
          ? { transform: `translate(${offset}px, ${leaving ? 0 : drag.y}px) rotate(${offset / 18}deg)`, transition: drag.active && !leaving ? 'none' : 'transform .22s ease' }
          : { transform: `translateY(${depth * 10}px) scale(${1 - depth * 0.04})` }
        return <article key={card.id} className={`swipe-card ${isTop ? 'top' : ''}`} style={style}
          onPointerDown={isTop ? onPointerDown : undefined} onPointerMove={isTop ? onPointerMove : undefined} onPointerUp={isTop ? onPointerUp : undefined} onPointerCancel={isTop ? onPointerUp : undefined}
          aria-hidden={!isTop}>
          <div className={`swipe-art art-${ART[card.category] || 'default'}`}>
            <span className="swipe-category">{titleCase(card.category)}</span>
            {card.scores?.wish && <span className="swipe-wish">for: {card.scores.wish}</span>}
            {isTop && hint && <span className={`swipe-stamp ${hint}`}>{hint === 'like' ? 'GO' : 'SKIP'}</span>}
          </div>
          <div className="swipe-body">
            <h3>{card.name}</h3>
            <PlaceFacts place={card} />
            <div className="swipe-badges"><ConfidenceBadge place={card} /></div>
            {card.reasons?.length > 0 && <ul className="swipe-reasons">{card.reasons.slice(0, 3).map((reason) => <li key={reason}>{reason}</li>)}</ul>}
            {isTop && <button type="button" className="link-button" onClick={() => onOpen(card)}>More about this place</button>}
          </div>
        </article>
      })}
    </div>
    <div className="swipe-actions">
      <button type="button" className="swipe-button pass" aria-label="Skip this place" disabled={!top} onClick={() => decide('pass')}><X size={26} /></button>
      <button type="button" className="swipe-button undo" aria-label="Undo last swipe" disabled={!Object.keys(decisions).length} onClick={onUndo}><RotateCcw size={18} /></button>
      <button type="button" className="swipe-button like" aria-label="Go to this place" disabled={!top} onClick={() => decide('like')}><Heart size={26} /></button>
    </div>
  </section>
}
