"""Speech module initialization."""
from __future__ import annotations

from app.speech.providers import HuggingFaceWhisperProvider, SpeechError, SpeechProvider

__all__ = ["SpeechProvider", "HuggingFaceWhisperProvider", "SpeechError"]
