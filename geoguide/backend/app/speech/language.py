"""Language identification and translation layer for speech inputs.

Accurately distinguishes between English, Indian vernacular languages
(Hindi, Kannada, Tamil, Telugu, Malayalam, Marathi, Bengali, Gujarati,
Punjabi, Odia, Assamese, Urdu), and code-mixed speech (Hinglish, Kanglish, etc.).

Preserves place names, proper nouns, and traveller intent.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from app.llm.client import llm

logger = logging.getLogger(__name__)

# Unicode script ranges for Indian scripts
SCRIPT_MAP: list[tuple[str, str, int, int]] = [
    ("kn", "Kannada", 0x0C80, 0x0CFF),
    ("hi", "Hindi", 0x0900, 0x097F),  # Devanagari (Hindi, Marathi)
    ("ta", "Tamil", 0x0B80, 0x0BFF),
    ("te", "Telugu", 0x0C00, 0x0C7F),
    ("ml", "Malayalam", 0x0D00, 0x0D7F),
    ("bn", "Bengali", 0x0980, 0x09FF),
    ("gu", "Gujarati", 0x0A80, 0x0AFF),
    ("pa", "Punjabi", 0x0A00, 0x0A7F),  # Gurmukhi
    ("or", "Odia", 0x0B00, 0x0B7F),
    ("ur", "Urdu", 0x0600, 0x06FF),    # Arabic script
]

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "kn": "Kannada",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "or": "Odia",
    "as": "Assamese",
    "ur": "Urdu",
}


def detect_script(text: str) -> tuple[str, str] | None:
    """Fast deterministic script detection from Unicode character ranges."""
    counts: dict[str, int] = {}
    for char in text:
        cp = ord(char)
        for code, name, start, end in SCRIPT_MAP:
            if start <= cp <= end:
                counts[code] = counts.get(code, 0) + 1
                break

    if not counts:
        return None

    best_code = max(counts, key=counts.get)
    # Require at least 2 script characters to avoid stray punctuation
    if counts[best_code] >= 2:
        return best_code, LANGUAGE_NAMES.get(best_code, best_code.upper())
    return None


def analyze_speech_text(text: str, target_language: str = "en") -> dict[str, Any]:
    """Identify the spoken language, handle code-mixing, and translate to English if needed.

    Preserves original text, proper nouns, and traveller constraints.
    """
    clean = text.strip()
    if not clean:
        return {
            "language": "en",
            "language_name": "English",
            "is_english": True,
            "original_text": "",
            "translated_text": "",
            "normalized_query": "",
        }

    script_detected = detect_script(clean)

    # Use LLM to accurately identify code-mixing (Hinglish/Kanglish), detect language,
    # normalize, and translate while preserving proper nouns.
    if llm.configured:
        prompt = (
            f"You are the multilingual language layer for GeoGuide, a travel companion.\n"
            f"Transcribed speech from a traveller:\n"
            f"\"{clean}\"\n\n"
            f"Analyze the speech and return a strictly valid JSON object with:\n"
            f"- \"detected_language\": 2-letter ISO 639-1 code (e.g. 'en', 'hi', 'kn', 'ta', 'te', 'ml', 'mr', 'bn', 'gu', 'pa', 'ur', 'or', 'as')\n"
            f"- \"language_name\": full English name of the language (e.g. 'Kannada', 'Hindi', 'Tamil', 'English')\n"
            f"- \"is_english\": boolean (true ONLY if the speech is predominantly in English with English vocabulary)\n"
            f"- \"is_code_mixed\": boolean (true if Hinglish, Kanglish, Tanglish, or other code-switching is present)\n"
            f"- \"translated_text\": high-quality English translation of the speech. If already in English, keep the exact meaning. "
            f"CRITICAL: Keep Indian proper nouns, monument names, locality names, and place names verbatim (e.g. 'Cubbon Park', 'Lalbagh', 'Indiranagar', 'Jayanagar', 'MG Road', 'Vidhana Soudha'). Never translate place names into generic words.\n"
            f"- \"normalized_query\": a clean, concise English search/intent query suitable for place and travel recommendation retrieval (e.g. 'good coffee shops near me in Bangalore').\n"
        )
        try:
            res = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=300)
            lang = str(res.get("detected_language") or (script_detected[0] if script_detected else "en")).lower().strip()
            # Canonicalize 2-letter code
            if len(lang) > 2 and lang in {"kannada", "hindi", "tamil", "telugu", "english"}:
                lang = {"kannada": "kn", "hindi": "hi", "tamil": "ta", "telugu": "te", "english": "en"}[lang]
            lang_name = res.get("language_name") or LANGUAGE_NAMES.get(lang, lang.capitalize())
            is_en = bool(res.get("is_english")) if "is_english" in res else (lang == "en")
            translated = str(res.get("translated_text") or clean).strip()
            normalized = str(res.get("normalized_query") or translated).strip()

            return {
                "language": lang,
                "language_name": lang_name,
                "is_english": is_en,
                "is_code_mixed": bool(res.get("is_code_mixed")),
                "original_text": clean,
                "translated_text": translated,
                "normalized_query": normalized,
            }
        except Exception as exc:
            logger.warning("speech_language_llm_fallback: %s", exc)

    # Deterministic fallback when LLM is unavailable or offline
    if script_detected:
        code, name = script_detected
        return {
            "language": code,
            "language_name": name,
            "is_english": False,
            "is_code_mixed": False,
            "original_text": clean,
            "translated_text": clean,
            "normalized_query": clean,
        }

    return {
        "language": "en",
        "language_name": "English",
        "is_english": True,
        "is_code_mixed": False,
        "original_text": clean,
        "translated_text": clean,
        "normalized_query": clean,
    }
