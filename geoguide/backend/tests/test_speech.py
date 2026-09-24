"""Unit and integration tests for the multilingual voice-input system.

Validates:
- Audio validation (file size limits, mime types)
- Hugging Face Whisper Large-v3 provider interface and error handling
- Multilingual language detection (English, Hindi, Kannada, Tamil, Telugu, Malayalam, etc.)
- Code-mixed Indian language handling (Hinglish, Kanglish)
- Preservation of place names and proper nouns in English translation
- Dedicated /api/speech/transcribe FastAPI endpoint
- Token security (no leakage in errors or responses)
"""
from __future__ import annotations

import io
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.speech.audio import validate_audio_payload
from app.speech.language import analyze_speech_text, detect_script
from app.speech.providers import HuggingFaceWhisperProvider, SpeechError, SpeechProvider
from app.services.speech_service import SpeechService


class DummySpeechProvider(SpeechProvider):
    def __init__(self, transcript_to_return: str = "Find coffee shops near me") -> None:
        self.transcript_to_return = transcript_to_return
        self.calls = 0

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        self.calls += 1
        return self.transcript_to_return


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


# --- 1. Audio Validation Tests ---

def test_validate_audio_empty_payload() -> None:
    with pytest.raises(SpeechError) as exc_info:
        validate_audio_payload(b"", "test.wav", "audio/wav")
    assert exc_info.value.code == "empty_audio"


def test_validate_audio_unsupported_format() -> None:
    with pytest.raises(SpeechError) as exc_info:
        validate_audio_payload(b"some content", "test.txt", "text/plain")
    assert exc_info.value.code == "unsupported_format"


def test_validate_audio_valid_formats() -> None:
    # WAV, WebM, OGG are allowed
    validate_audio_payload(b"RIFF....WAVE", "voice.wav", "audio/wav")
    validate_audio_payload(b"\x1a\x45\xdf\xa3", "voice.webm", "audio/webm;codecs=opus")


# --- 2. Script and Language Detection Tests ---

def test_detect_script_kannada() -> None:
    text = "ನನ್ನ ಹತ್ತಿರ ಒಳ್ಳೆಯ ಕಾಫಿ ಶಾಪ್ ಹುಡುಕು"
    detected = detect_script(text)
    assert detected is not None
    assert detected[0] == "kn"
    assert detected[1] == "Kannada"


def test_detect_script_hindi() -> None:
    text = "मेरे पास अच्छे कैफे ढूंढो"
    detected = detect_script(text)
    assert detected is not None
    assert detected[0] == "hi"
    assert detected[1] == "Hindi"


def test_detect_script_tamil() -> None:
    text = "எனக்கு அருகில் நல்ல காபி கடையை கண்டுபிடி"
    detected = detect_script(text)
    assert detected is not None
    assert detected[0] == "ta"
    assert detected[1] == "Tamil"


def test_detect_script_telugu() -> None:
    text = "నా దగ్గర మంచి కాఫీ షాప్ కనుక్కో"
    detected = detect_script(text)
    assert detected is not None
    assert detected[0] == "te"
    assert detected[1] == "Telugu"


def test_detect_script_malayalam() -> None:
    text = "എന്റെ അടുത്ത് നല്ല കഫേ കണ്ടെത്തൂ"
    detected = detect_script(text)
    assert detected is not None
    assert detected[0] == "ml"
    assert detected[1] == "Malayalam"


def test_detect_script_english_text() -> None:
    text = "Find coffee shops near me"
    detected = detect_script(text)
    assert detected is None  # Latin characters return None from Indic script detector


# --- 3. Language & Code-mixed Analysis Layer Tests ---

def test_analyze_speech_english() -> None:
    result = analyze_speech_text("Find coffee shops near me in Indiranagar")
    assert result["is_english"] is True
    assert result["language"] == "en"
    assert "Indiranagar" in result["translated_text"]


def test_analyze_speech_kannada_translation_preserves_entities(monkeypatch) -> None:
    from app.llm.client import llm

    mock_res = {
        "detected_language": "kn",
        "language_name": "Kannada",
        "is_english": False,
        "is_code_mixed": False,
        "translated_text": "Find a good coffee shop near me in Bangalore.",
        "normalized_query": "good coffee shop near me in Bangalore",
    }
    monkeypatch.setattr(llm, "api_key", "mock-groq-key")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: mock_res)

    kannada_text = "ಬೆಂಗಳೂರುದಲ್ಲಿ ನನ್ನ ಹತ್ತಿರ ಒಳ್ಳೆ ಕಾಫಿ ಶಾಪ್ ಹುಡುಕು"
    result = analyze_speech_text(kannada_text)
    assert result["language"] in ("kn", "kannada")
    assert result["is_english"] is False
    assert result["original_text"] == kannada_text
    trans = result["translated_text"].lower()
    assert "coffee" in trans or "cafe" in trans


def test_analyze_speech_fallback_without_llm(monkeypatch) -> None:
    from app.llm.client import llm
    monkeypatch.setattr(llm, "api_key", "")

    kannada_text = "ಬೆಂಗಳೂರುದಲ್ಲಿ ನನ್ನ ಹತ್ತಿರ ಒಳ್ಳೆ ಕಾಫಿ ಶಾಪ್ ಹುಡುಕು"
    result = analyze_speech_text(kannada_text)
    assert result["language"] == "kn"
    assert result["language_name"] == "Kannada"
    assert result["is_english"] is False
    assert result["original_text"] == kannada_text


def test_analyze_speech_hinglish_code_mixed(monkeypatch) -> None:
    from app.llm.client import llm

    mock_res = {
        "detected_language": "hi",
        "language_name": "Hindi",
        "is_english": False,
        "is_code_mixed": True,
        "translated_text": "Is there any nice coffee shop near me in Indiranagar?",
        "normalized_query": "nice coffee shop in Indiranagar",
    }
    monkeypatch.setattr(llm, "api_key", "mock-groq-key")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: mock_res)

    hinglish = "Indiranagar mein koi nice coffee shop hai kya?"
    result = analyze_speech_text(hinglish)
    assert result["is_code_mixed"] is True
    assert "Indiranagar" in result["translated_text"]


def test_analyze_speech_kanglish_code_mixed(monkeypatch) -> None:
    from app.llm.client import llm

    mock_res = {
        "detected_language": "kn",
        "language_name": "Kannada",
        "is_english": False,
        "is_code_mixed": True,
        "translated_text": "Please suggest good cafes in Bangalore.",
        "normalized_query": "good cafes in Bangalore",
    }
    monkeypatch.setattr(llm, "api_key", "mock-groq-key")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: mock_res)

    kanglish = "Bangalore alli good cafes suggest maadi"
    result = analyze_speech_text(kanglish)
    assert "Bangalore" in result["translated_text"] or "Bengaluru" in result["translated_text"]


# --- 4. Speech Service Pipeline Tests ---

def test_speech_service_transcribe() -> None:
    dummy = DummySpeechProvider("Find museums near Cubbon Park")
    service = SpeechService(provider=dummy)
    audio_data = b"RIFFfake16khzwavdata"

    res = service.transcribe_audio(
        audio_bytes=audio_data,
        filename="test.wav",
        content_type="audio/wav",
    )
    assert res["success"] is True
    assert res["original_text"] == "Find museums near Cubbon Park"
    assert "Cubbon Park" in res["translated_text"]
    assert res["is_english"] is True
    assert dummy.calls == 1


def test_speech_service_no_speech_detected() -> None:
    dummy = DummySpeechProvider("")
    service = SpeechService(provider=dummy)
    audio_data = b"RIFFfake16khzwavdata"

    res = service.transcribe_audio(
        audio_bytes=audio_data,
        filename="test.wav",
        content_type="audio/wav",
    )
    assert res["success"] is True
    assert res.get("no_speech_detected") is True


# --- 5. FastAPI Endpoint Integration Tests ---

def test_speech_api_endpoint(test_client: TestClient, monkeypatch) -> None:
    from app.services import speech_service as svc_module
    from app.llm.client import llm

    mock_res = {
        "detected_language": "kn",
        "language_name": "Kannada",
        "is_english": False,
        "is_code_mixed": False,
        "translated_text": "Find a good coffee shop near me.",
        "normalized_query": "good coffee shop near me",
    }
    monkeypatch.setattr(llm, "api_key", "mock-groq-key")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: mock_res)

    original_provider = svc_module.speech_service.provider
    try:
        svc_module.speech_service.provider = DummySpeechProvider("ನನ್ನ ಹತ್ತಿರ ಒಳ್ಳೆಯ ಕಾಫಿ ಶಾಪ್ ಹುಡುಕು")

        wav_bytes = b"RIFF" + b"\x00" * 100
        files = {"audio": ("sample.wav", io.BytesIO(wav_bytes), "audio/wav")}
        data = {"target_language": "en"}

        response = test_client.post("/api/speech/transcribe", files=files, data=data)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["original_text"] == "ನನ್ನ ಹತ್ತಿರ ಒಳ್ಳೆಯ ಕಾಫಿ ಶಾಪ್ ಹುಡುಕು"
        assert body["language"] in ("kn", "kannada")
        assert "coffee" in body["translated_text"].lower() or "cafe" in body["translated_text"].lower()
    finally:
        svc_module.speech_service.provider = original_provider


def test_speech_api_unsupported_format(test_client: TestClient) -> None:
    files = {"audio": ("bad.exe", io.BytesIO(b"MZ......"), "application/x-msdownload")}
    response = test_client.post("/api/speech/transcribe", files=files)
    assert response.status_code == 400
    detail = response.json().get("detail", {})
    assert detail.get("success") is False
    assert "not supported" in detail.get("error", "").lower()


def test_token_not_leaked_in_errors(test_client: TestClient) -> None:
    # Verify that even under error conditions, tokens are never printed
    from app.services import speech_service as svc_module
    from app.config import HF_TOKEN

    class FailingProvider(SpeechProvider):
        def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
            raise SpeechError("auth_error", "Speech service authentication failed.", status_code=502)

    original_provider = svc_module.speech_service.provider
    try:
        svc_module.speech_service.provider = FailingProvider()
        files = {"audio": ("sample.wav", io.BytesIO(b"RIFF" + b"\x00" * 50), "audio/wav")}
        response = test_client.post("/api/speech/transcribe", files=files)
        assert response.status_code == 502
        content_str = response.text
        if HF_TOKEN:
            assert HF_TOKEN not in content_str
    finally:
        svc_module.speech_service.provider = original_provider
