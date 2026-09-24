// Renders the small markdown subset GeoGuide answers use (bold, bullets) and turns
// citation markers like [E3] into chips that reveal their source. No raw HTML is injected.

function Inline({ text, sources, onCite }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\[E\d+\])/g).filter(Boolean)
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    const cite = /^\[(E\d+)\]$/.exec(part)
    if (cite) {
      const source = sources?.find((item) => item.id === cite[1])
      if (!source) return null
      return <button key={index} type="button" className="cite-chip" title={`${source.title} — ${source.source || source.source_type}`} onClick={() => onCite?.(source)}>{cite[1].slice(1)}</button>
    }
    return <span key={index}>{part}</span>
  })
}

export default function RichText({ text, sources, onCite }) {
  const lines = (text || '').split('\n')
  const blocks = []
  let bullets = []
  const flush = () => {
    if (bullets.length) blocks.push(<ul key={`ul-${blocks.length}`}>{bullets}</ul>)
    bullets = []
  }
  lines.forEach((line, index) => {
    const trimmed = line.trim()
    if (!trimmed) { flush(); return }
    const bullet = /^[-*•]\s+(.*)$/.exec(trimmed)
    if (bullet) {
      bullets.push(<li key={index}><Inline text={bullet[1]} sources={sources} onCite={onCite} /></li>)
    } else {
      flush()
      blocks.push(<p key={index}><Inline text={trimmed} sources={sources} onCite={onCite} /></p>)
    }
  })
  flush()
  return <div className="rich-text">{blocks}</div>
}
