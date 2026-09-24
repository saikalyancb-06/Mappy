"""Speech service orchestrator for GeoGuide.

Coordinates audio decoding, Hugging Face Whisper Large-v3 transcription,
multilingual language identification, translation to English, query normalization,
and structured logging.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.config import HF_PROVIDER, WHISPER_MODEL
from app.speech.audio import convert_to_wav, validate_audio_payload
from app.speech.language import analyze_speech_text
from app.speech.providers import HuggingFaceWhisperProvider, SpeechError, SpeechProvider

logger = logging.getLogger(__name__)


class SpeechService:
    def __init__(self, provider: SpeechProvider | None = None) -> None:
        self.provider = provider or HuggingFaceWhisperProvider()

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        filename: str | None = None,
        content_type: str | None = None,
        target_language: str = "en",
    ) -> dict[str, Any]:
        """Process and transcribe speech audio end-to-end.

        Returns structured dict with:
        - success: bool
        - text / original_text: str
        - language: str (e.g. 'kn', 'hi', 'en')
        - language_name: str (e.g. 'Kannada', 'Hindi')
        - translated_text: str
        - normalized_query: str
        - model: str
        - provider: str
        - latency_ms: int
        """
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()

        # 1. Validate payload
        validate_audio_payload(audio_bytes, filename, content_type)

        # 2. Convert to standard 16kHz WAV
        wav_bytes, duration_s = convert_to_wav(audio_bytes, content_type)

        # 3. Transcribe via Whisper large-v3 provider
        transcription_start = time.monotonic()
        raw_transcript = self.provider.transcribe(wav_bytes, mime_type="audio/wav")
        asr_latency_ms = int((time.monotonic() - transcription_start) * 1000)

        # 4. Check for speech presence
        if not raw_transcript or not raw_transcript.strip():
            logger.info(
                "speech_transcription_empty req_id=%s duration_s=%s asr_latency_ms=%d",
                request_id,
                f"{duration_s:.1f}" if duration_s else "unknown",
                asr_latency_ms,
            )
            return {
                "success": True,
                "text": "",
                "original_text": "",
                "translated_text": "",
                "normalized_query": "",
                "language": "en",
                "language_name": "English",
                "is_english": True,
                "is_code_mixed": False,
                "no_speech_detected": True,
                "model": WHISPER_MODEL,
                "provider": HF_PROVIDER,
                "duration_seconds": round(duration_s, 2) if duration_s else None,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }

        # 5. Language detection and translation layer
        analysis = analyze_speech_text(raw_transcript, target_language=target_language)
        total_latency_ms = int((time.monotonic() - started) * 1000)

        # 6. Structured logging (never logs HF_TOKEN or raw audio)
        logger.info(
            "speech_transcription req_id=%s duration_s=%s lang=%s code_mixed=%s asr_latency_ms=%d total_latency_ms=%d model=%s provider=%s",
            request_id,
            f"{duration_s:.1f}" if duration_s else "unknown",
            analysis["language"],
            analysis.get("is_code_mixed", False),
            asr_latency_ms,
            total_latency_ms,
            WHISPER_MODEL,
            HF_PROVIDER,
        )

        return {
            "success": True,
            "text": analysis["original_text"],
            "original_text": analysis["original_text"],
            "language": analysis["language"],
            "language_name": analysis["language_name"],
            "is_english": analysis["is_english"],
            "is_code_mixed": analysis.get("is_code_mixed", False),
            "translated_text": analysis["translated_text"],
            "normalized_query": analysis["normalized_query"],
            "model": WHISPER_MODEL,
            "provider": HF_PROVIDER,
            "duration_seconds": round(duration_s, 2) if duration_s else None,
            "latency_ms": total_latency_ms,
        }


speech_service = SpeechService()
