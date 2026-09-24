"""Audio validation, conversion and inspection utilities."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from app.config import SPEECH_MAX_DURATION_SECONDS, SPEECH_MAX_FILE_SIZE_BYTES
from app.speech.providers import SpeechError

logger = logging.getLogger(__name__)

SUPPORTED_MIME_PREFIXES = (
    "audio/",
    "video/webm",
    "video/ogg",
    "application/octet-stream",
)


def validate_audio_payload(content: bytes, filename: str | None, content_type: str | None) -> None:
    """Validate audio file size and format."""
    if not content:
        raise SpeechError("empty_audio", "The uploaded audio file is empty.", status_code=400)

    if len(content) > SPEECH_MAX_FILE_SIZE_BYTES:
        max_mb = SPEECH_MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise SpeechError("file_too_large", f"Audio file exceeds maximum allowed size ({max_mb} MB).", status_code=413)

    ct = (content_type or "").lower().split(";")[0].strip()
    ext = os.path.splitext(filename or "")[1].lower()

    valid_exts = {".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac", ".aac", ".mp4"}
    is_valid_type = any(ct.startswith(prefix) for prefix in SUPPORTED_MIME_PREFIXES) or ext in valid_exts
    if not is_valid_type:
        raise SpeechError("unsupported_format", f"Audio format '{ct or ext}' is not supported.", status_code=400)


def inspect_audio_duration(file_path: str) -> float | None:
    """Inspect duration in seconds using ffprobe if installed."""
    if not shutil.which("ffprobe"):
        return None

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode == 0 and proc.stdout.strip():
            return float(proc.stdout.strip())
    except Exception as exc:
        logger.debug("ffprobe_duration_failed: %s", exc)
    return None


def convert_to_wav(content: bytes, original_mime: str | None = None) -> tuple[bytes, float | None]:
    """Convert audio payload to 16kHz mono WAV using ffmpeg if available.

    Returns (wav_bytes, duration_seconds).
    """
    if not shutil.which("ffmpeg"):
        return content, None

    with tempfile.NamedTemporaryFile(suffix=".in", delete=False) as in_f:
        in_path = in_f.name
        in_f.write(content)

    out_path = in_path + ".wav"
    try:
        duration = inspect_audio_duration(in_path)
        if duration and duration > SPEECH_MAX_DURATION_SECONDS:
            raise SpeechError(
                "audio_too_long",
                f"Audio recording duration ({int(duration)}s) exceeds maximum allowed duration ({int(SPEECH_MAX_DURATION_SECONDS)}s).",
                status_code=400,
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i", in_path,
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            out_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0:
            logger.warning("ffmpeg_convert_failed stderr=%s", proc.stderr.decode("utf-8", errors="replace")[:200])
            # Return original if ffmpeg couldn't convert
            return content, duration

        with open(out_path, "rb") as out_f:
            wav_bytes = out_f.read()

        if not duration:
            duration = inspect_audio_duration(out_path)

        return wav_bytes, duration
    finally:
        for p in (in_path, out_path):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
