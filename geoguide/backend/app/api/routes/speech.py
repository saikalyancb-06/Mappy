"""FastAPI router for speech transcription endpoint."""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.speech.providers import SpeechError
from app.services.speech_service import speech_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.post("/transcribe")
async def transcribe(
    audio: Annotated[UploadFile, File(description="The audio file to transcribe")],
    target_language: Annotated[str, Form(description="Target processing language (default 'en')")] = "en",
) -> dict:
    """Accept microphone audio and transcribe via Whisper large-v3 with language detection."""
    if not audio:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "No audio file provided."},
        )

    try:
        content = await audio.read()
    except Exception as exc:
        logger.warning("speech_audio_read_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"success": False, "error": "Could not read uploaded audio content."},
        ) from None

    try:
        result = speech_service.transcribe_audio(
            audio_bytes=content,
            filename=audio.filename,
            content_type=audio.content_type,
            target_language=target_language,
        )
        return result
    except SpeechError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"success": False, "error": exc.message, "code": exc.code},
        ) from None
    except Exception as exc:
        logger.error("speech_endpoint_error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"success": False, "error": "Speech transcription service is temporarily unavailable."},
        ) from None
