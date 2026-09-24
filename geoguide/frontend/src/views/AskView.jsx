import { useEffect, useRef, useState } from 'react'
import { Bug, Mic, Send, Sparkles, Volume2, X } from 'lucide-react'
import { askGeoGuide } from '../api'
import RichText from '../components/RichText'
import VoiceInputButton from '../components/VoiceInputButton'
import { Chip, ConfidenceBadge, EventCard, IconCircleButton, PlaceFacts } from '../components/ui'
import { formatDay, formatDistance, titleCase } from '../format'

const SPEECH_LANG = { en: 'en-IN', kn: 'kn-IN', hi: 'hi-IN' }
const SUGGESTIONS = ["What's happening here this weekend?", 'Any festivals this month?', 'I have 2 hours and ₹800, something peaceful', 'Somewhere open now, not crowded, good reviews, 20 min by bike', 'Give me 3 hidden gems only', 'Coffee shops near me', 'Why is this place historically important?', 'Which places are step-free?']

const Recognition = typeof window !== 'undefined' ? window.SpeechRecognition || window.webkitSpeechRecognition : null

function contextLine(response) {
  const reference = response.geo_context?.reference
  const parts = [titleCase(response.intent?.intent?.toLowerCase())]
  const lookup = ['PLACE_LOOKUP', 'PLACE_DISAMBIGUATION'].includes(response.intent?.intent)
  if (reference && !lookup) parts.push(reference.origin === 'user_location' ? 'around you' : `${reference.semantic === 'near_place' ? 'around' : 'in'} ${reference.label}`)
  if (response.geo_context?.location_status === 'unavailable') parts.push('your location is off')
  parts.push(`confidence ${Math.round((response.confidence || 0) * 100)}%`)
  return parts.join(' · ')
}

export default function AskView({ context, selectedDate, onClearDate, language, selectedPlace, onClearSelected, onAdoptDestination, onOpen, debugAvailable }) {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const [listening, setListening] = useState(false)
  const [debug, setDebug] = useState(false)
  const [openSource, setOpenSource] = useState(null)
  const endRef = useRef(null)

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const send = async (text) => {
    const next = (text ?? question).trim()
    if (!next || sending) return
    setQuestion('')
    setMessages((current) => [...current, { role: 'user', text: next }])
    setSending(true)
    try {
      const response = await askGeoGuide({ question: next, context, selectedPlaceId: selectedPlace?.id?.startsWith('web-') ? null : selectedPlace?.id, language, debug, date: selectedDate })
      setMessages((current) => [...current, { role: 'assistant', response }])
      if (response.next_active_destination && response.next_active_destination.destination_id !== context.destination?.destination_id) onAdoptDestination(response.next_active_destination)
    } catch (requestError) {
      setMessages((current) => [...current, { role: 'assistant', error: requestError.message }])
    } finally {
      setSending(false)
    }
  }

  const listen = () => {
    if (!Recognition) return
    const recognition = new Recognition()
    recognition.lang = SPEECH_LANG[language] || 'en-IN'
    recognition.interimResults = false
    recognition.onresult = (event) => { const heard = event.results[0][0].transcript; setQuestion(heard); send(heard) }
    recognition.onend = () => setListening(false)
    recognition.onerror = () => setListening(false)
    setListening(true)
    recognition.start()
  }

  const speak = (text) => {
    if (!('speechSynthesis' in window)) return
    const utterance = new SpeechSynthesisUtterance(text.replace(/\[E\d+\]/g, '').replaceAll('**', ''))
    utterance.lang = SPEECH_LANG[language] || 'en-IN'
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  }

  return <div className="view-content ask-view">
    <header className="simple-header"><div><span className="eyebrow">Grounded answers</span><h1>Ask GeoGuide</h1></div>
      <div className="header-actions">
        {debugAvailable && <IconCircleButton label={debug ? 'Hide retrieval trace' : 'Show retrieval trace'} variant={debug ? 'dark' : 'light'} onClick={() => setDebug((value) => !value)}><Bug size={18} /></IconCircleButton>}
        <VoiceInputButton
          compact
          onTranscript={(queryText, speechResult) => {
            setQuestion(queryText)
            send(queryText)
          }}
          buttonLabel="Whisper voice input"
        />
      </div>
    </header>
    {selectedDate && <div className="selected-place"><span>Answering for <strong>{formatDay(selectedDate)}</strong> (from Explore)</span><button type="button" aria-label="Use today instead" onClick={onClearDate}><X size={14} /></button></div>}
    {selectedPlace && <div className="selected-place"><span>Asking about <strong>{selectedPlace.name}</strong></span><button type="button" aria-label="Stop asking about this place" onClick={onClearSelected}><X size={14} /></button></div>}
    {!messages.length && <div className="ask-intro"><Sparkles size={26} /><h2>What do you want to know?</h2><p>Answers come from verified place data, live weather and search, with sources you can check.</p><div className="suggestion-row wrap">{SUGGESTIONS.map((text) => <Chip key={text} onClick={() => send(text)}>{text}</Chip>)}</div></div>}
    <div className="message-list">
      {messages.map((message, index) => {
        if (message.role === 'user') return <div className="message user" key={index}><p>{message.text}</p></div>
        if (message.error) return <div className="message assistant" key={index}><p>{message.error}</p><small>Unavailable</small></div>
        const { response } = message
        return <div className="message assistant" key={index}>
          <RichText text={response.answer} sources={response.sources} onCite={setOpenSource} />
          {response.understood?.length > 0 && <div className="understood-inline">{response.understood.map((item) => <span className="understood-chip" key={item}>{item}</span>)}</div>}
          {response.event_listing && <div className="ask-events">
            {response.event_listing.event_status === 'no_verified_events_found' && <div className="empty-events"><strong>No verified events</strong><p>{response.event_listing.message}</p></div>}
            {response.event_listing.groups.map((group) => <div key={group.id} className="event-group"><span className="eyebrow">{group.label}</span>{response.events.filter((e) => group.event_ids.includes(e.id)).map((event) => <EventCard key={event.id} event={event} />)}</div>)}
          </div>}
          {response.route && <p className="route-summary">From {response.route.origin} to {response.route.destination}: ~{response.route.direct_min} min by {response.route.mode} (estimate), detours up to {response.route.max_detour_min} min</p>}
          {response.comparison && <div className="compare-wrap"><table className="compare-table"><thead><tr><th>Factor</th>{response.comparison.columns.map((name) => <th key={name}>{name}</th>)}</tr></thead><tbody>{response.comparison.rows.map((row) => <tr key={row.factor}><td>{row.factor}</td>{row.values.map((value, i) => <td key={i}>{value == null ? '—' : row.format === 'km' ? formatDistance(value) : row.format === 'bar' ? <span className="mini-bar"><span style={{ width: `${Math.round(value * 100)}%` }} /></span> : titleCase(String(value))}</td>)}</tr>)}</tbody></table></div>}
          {response.clarification && <div className="suggestion-row wrap">{response.clarification.options.map((option) => <Chip key={option.id} onClick={() => send(`Where is ${option.name}${option.address ? ` at ${option.address}` : ''}?`)}>{option.name}</Chip>)}</div>}
          {response.results?.length > 0 && !['PLACE_DISAMBIGUATION', 'COMPARE'].includes(response.intent.intent) && <div className="ask-results">{response.results.slice(0, 5).map((result) => <button type="button" className="ask-result" key={result.id} onClick={() => onOpen(result)}><strong>{result.name}</strong><PlaceFacts place={result} showUserDistance={false} /><ConfidenceBadge place={result} /></button>)}</div>}
          {response.notices?.length > 0 && <small className="notice-text">{response.notices.join(' ')}</small>}
          <small className="answer-meta">{contextLine(response)} · {response.answer_meta.mode === 'offline' ? 'answered offline from your saved guide' : response.answer_meta.mode === 'deterministic' ? 'assembled from verified data' : 'AI summary, checked against sources'}<button type="button" className="speak-button" aria-label="Read aloud" onClick={() => speak(response.answer)}><Volume2 size={13} /></button></small>
          {response.debug && <details className="debug-trace"><summary>Retrieval trace ({response.debug.total_ms} ms)</summary><pre>{JSON.stringify(response.debug, null, 2)}</pre></details>}
        </div>
      })}
      {sending && <div className="message assistant typing"><p>Checking sources…</p></div>}
      <div ref={endRef} />
    </div>
    {openSource && <div className="source-sheet" role="dialog" aria-label="Source"><button type="button" className="context-clear" aria-label="Close source" onClick={() => setOpenSource(null)}><X size={14} /></button><span className="eyebrow">{titleCase(openSource.source_type)} · {openSource.source || 'GeoGuide data'}</span><strong>{openSource.title}</strong><p>{openSource.content}</p>{openSource.source_url && <a href={openSource.source_url} target="_blank" rel="noopener noreferrer">Open source</a>}{openSource.retrieved_at && <small>Retrieved {openSource.retrieved_at.slice(0, 16).replace('T', ' ')}</small>}</div>}
    <div className="ask-composer-wrap">
      <form className="ask-composer" onSubmit={(event) => { event.preventDefault(); send() }}>
        <input aria-label="Ask GeoGuide" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={selectedPlace ? `Ask about ${selectedPlace.name}…` : 'Ask about places, timing, weather…'} />
        <VoiceInputButton
          compact
          onTranscript={(queryText) => {
            setQuestion(queryText)
            send(queryText)
          }}
          buttonLabel="Voice question"
        />
        <button aria-label="Send question" className="send-button" disabled={sending || !question.trim()} type="submit"><Send size={18} /></button>
      </form>
    </div>
  </div>
}
