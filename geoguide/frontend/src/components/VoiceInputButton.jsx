import { useState } from 'react'
import { Check, Edit2, Loader2, Mic, MicOff, Sparkles, X } from 'lucide-react'
import { transcribeAudio } from '../api'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'

/**
 * VoiceInputButton: Multilingual voice-input trigger and confirmation card.
 * Uses Hugging Face Whisper Large-v3 on backend.
 * Never exposes HF_TOKEN on client.
 */
export default function VoiceInputButton({
  onTranscript,
  targetLanguage = 'en',
  className = '',
  buttonLabel = 'Speak',
  compact = false,
}) {
  const { recording, error: micError, startRecording, stopRecording, cancelRecording } = useVoiceRecorder()
  const [transcribing, setTranscribing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [isEditing, setIsEditing] = useState(false)
  const [draftText, setDraftText] = useState('')

  const handleStart = async (e) => {
    e?.preventDefault?.()
    setError('')
    setResult(null)
    setIsEditing(false)
    await startRecording()
  }

  const handleStop = async (e) => {
    e?.preventDefault?.()
    const blob = await stopRecording()
    if (!blob || blob.size === 0) return

    setTranscribing(true)
    setError('')
    try {
      const data = await transcribeAudio(blob, {
        targetLanguage,
        filename: blob.type.includes('ogg') ? 'voice.ogg' : 'voice.webm',
      })
      if (!data || !data.success) {
        throw new Error(data?.error || 'Speech transcription failed.')
      }
      if (data.no_speech_detected || (!data.text && !data.normalized_query)) {
        setError('No speech detected. Please try speaking again.')
        return
      }

      setResult(data)
      const textToUse = data.normalized_query || data.translated_text || data.original_text || data.text
      setDraftText(textToUse)
    } catch (err) {
      setError(err.message || 'Could not transcribe speech. Please try again.')
    } finally {
      setTranscribing(false)
    }
  }

  const handleUseQuery = (e) => {
    e?.preventDefault?.()
    if (!draftText.trim()) return
    onTranscript?.(draftText.trim(), result)
    setResult(null)
    setIsEditing(false)
    setDraftText('')
  }

  const handleCancelResult = (e) => {
    e?.preventDefault?.()
    setResult(null)
    setIsEditing(false)
    setDraftText('')
    setError('')
  }

  return (
    <div className={`voice-input-container ${className}`}>
      {!recording && !transcribing && (
        <button
          type="button"
          className={`voice-mic-btn ${compact ? 'compact' : ''}`}
          onClick={handleStart}
          aria-label={buttonLabel}
          title={buttonLabel}
        >
          <Mic size={18} />
          {!compact && <span>{buttonLabel}</span>}
        </button>
      )}

      {recording && (
        <button
          type="button"
          className="voice-mic-btn recording pulse"
          onClick={handleStop}
          aria-label="Stop recording"
          title="Press to stop recording"
        >
          <MicOff size={18} />
          <span>Stop · Listening…</span>
        </button>
      )}

      {transcribing && (
        <div className="voice-status-pill">
          <Loader2 size={16} className="spin-icon" />
          <span>Transcribing speech…</span>
        </div>
      )}

      {(micError || error) && (
        <div className="voice-error-banner" role="alert">
          <span>{micError || error}</span>
          <button type="button" onClick={() => { setError(''); cancelRecording() }} aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      )}

      {result && (
        <div className="voice-result-card" role="dialog" aria-label="Recognized Speech">
          <div className="voice-result-header">
            <div className="voice-detected-lang">
              <span className="voice-badge">
                <Sparkles size={13} /> Detected: <strong>{result.language_name || result.language}</strong>
                {result.is_code_mixed ? ' (Code-mixed)' : ''}
              </span>
            </div>
            <button type="button" className="voice-close-btn" onClick={handleCancelResult} aria-label="Close">
              <X size={14} />
            </button>
          </div>

          <div className="voice-original-speech">
            <label className="voice-label">Spoken:</label>
            <p className="voice-original-text">"{result.original_text || result.text}"</p>
          </div>

          {!result.is_english && result.translated_text && (
            <div className="voice-translated-speech">
              <label className="voice-label">English meaning:</label>
              <p className="voice-translated-text">{result.translated_text}</p>
            </div>
          )}

          {isEditing ? (
            <div className="voice-edit-field">
              <label className="voice-label">Edit query before submitting:</label>
              <input
                type="text"
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                autoFocus
                className="voice-input-edit"
              />
            </div>
          ) : null}

          <div className="voice-actions">
            <button type="button" className="primary-button small" onClick={handleUseQuery}>
              <Check size={14} /> Use query
            </button>
            <button
              type="button"
              className="secondary-button small"
              onClick={() => setIsEditing(!isEditing)}
            >
              <Edit2 size={13} /> {isEditing ? 'Done editing' : 'Edit'}
            </button>
            <button type="button" className="link-button" onClick={handleCancelResult}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
