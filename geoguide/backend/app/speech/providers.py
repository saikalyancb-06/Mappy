"""Speech provider abstractions and implementations.

Modular design: allows swapping Whisper with Sarvam Saaras or another
speech provider without rewriting business logic, routes, or frontend.
"""
from __future__ import annotations

import abc
import io
import logging
from typing import Any

from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError, InferenceTimeoutError

from app.config import HF_PROVIDER, HF_TOKEN, SPEECH_TIMEOUT_SECONDS, WHISPER_MODEL

logger = logging.getLogger(__name__)


class SpeechError(RuntimeError):
    """Clean domain error for speech service without leaking internal credentials."""

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class SpeechProvider(abc.ABC):
    @abc.abstractmethod
    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe audio into text preserving the spoken language."""
        raise NotImplementedError


class HuggingFaceWhisperProvider(SpeechProvider):
    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.token = token if token is not None else HF_TOKEN
        self.model = model or WHISPER_MODEL
        self.provider = provider or HF_PROVIDER
        self.timeout = timeout or SPEECH_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        return bool(self.token and self.token.strip())

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        if not self.configured:
            raise SpeechError("unconfigured", "Speech service is not configured. HF_TOKEN is missing.", status_code=503)

        # Standardize content-type for HF inference router
        content_type = mime_type.split(";")[0].strip().lower()
        if content_type in ("audio/webm", "video/webm"):
            content_type = "audio/webm"
        elif content_type in ("audio/x-wav", "audio/wave"):
            content_type = "audio/wav"
        elif not content_type:
            content_type = "audio/wav"

        client = InferenceClient(
            provider=self.provider,
            token=self.token,
            timeout=self.timeout,
            headers={"Content-Type": content_type},
        )

        try:
            # automatic_speech_recognition takes audio as bytes or file-like object
            result = client.automatic_speech_recognition(
                audio_bytes,
                model=self.model,
            )
            text = (result.text or "").strip()
            return text
        except InferenceTimeoutError:
            logger.warning("speech_hf_timeout model=%s provider=%s", self.model, self.provider)
            raise SpeechError("timeout", "Speech transcription request timed out.", status_code=504) from None
        except HfHubHTTPError as exc:
            status = getattr(exc.response, "status_code", 500) if hasattr(exc, "response") else 500
            err_msg = str(exc).lower()
            logger.warning("speech_hf_http_error status=%d", status)
            if status in (401, 403) or "unauthorized" in err_msg or "invalid token" in err_msg:
                raise SpeechError("auth_error", "Speech service authentication failed.", status_code=502) from None
            if status == 429 or "rate limit" in err_msg:
                raise SpeechError("rate_limited", "Speech service rate limit reached. Please try again shortly.", status_code=429) from None
            if status == 503 or "loading" in err_msg:
                raise SpeechError("provider_unavailable", "Speech model is currently loading or unavailable. Please retry in a few seconds.", status_code=503) from None
            if status == 400 and ("content type" in err_msg or "supported" in err_msg):
                raise SpeechError("unsupported_format", f"Audio format '{content_type}' is not supported by the speech provider.", status_code=400) from None
            raise SpeechError("inference_failed", "Speech transcription failed on the provider.", status_code=502) from None
        except Exception as exc:
            # Never leak HF_TOKEN or internal stack details in error messages
            logger.error("speech_hf_unexpected_error: %s", type(exc).__name__)
            raise SpeechError("service_error", "An error occurred while transcribing speech.", status_code=500) from None
