"""Prompt builder: the LLM receives bounded, structured evidence only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import Evidence

SYSTEM_PROMPT = """You are GeoGuide, a careful, friendly local travel companion.

Rules you must follow:
1. Answer ONLY from the EVIDENCE list. Never use outside knowledge about places, prices, hours, ratings, distances, weather or events.
2. After every factual statement add its citation marker, e.g. [E2]. Only cite ids that exist.
3. Never invent businesses, coordinates, ratings, distances, prices, opening hours or open/closed status. Quote numbers exactly as they appear in the evidence.
4. Keep the location context straight: say which place the answer is about. If LOCATION NOTES say the traveller's location is unavailable or stale, do not describe anything as "near you".
5. If the evidence does not cover part of the question, say so plainly and suggest what the traveller could check.
6. Preserve safety advisory severity; do not add warnings that are not in the evidence.
7. Be concise and practical: a one-sentence lead, then up to 5 bullet points. Put place names in **bold**. No raw JSON, ids or coordinates."""


@dataclass
class AnswerContext:
    question: str
    intent: str
    location_notes: list[str]
    evidence: list[Evidence]
    guidance: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def build_messages(context: AnswerContext, feedback: str | None = None) -> list[dict[str, str]]:
    evidence_lines = [f"[{item.id}] ({item.source_type}) {item.title}: {item.content}" for item in context.evidence]
    user = [
        f"QUESTION: {context.question}",
        f"INTENT: {context.intent}",
        "LOCATION NOTES:\n- " + "\n- ".join(context.location_notes or ["none"]),
    ]
    if context.guidance:
        user.append("ANSWER GUIDANCE:\n- " + "\n- ".join(context.guidance))
    user.append("EVIDENCE:\n" + "\n".join(evidence_lines))
    if feedback:
        user.append(f"YOUR PREVIOUS DRAFT HAD PROBLEMS, FIX THEM:\n{feedback}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(user)}]


TRANSLATE_PROMPT = """Translate the travel answer into {language}. Keep place names, monument names and other proper nouns in their original form (you may add the native-script form in brackets). Keep every citation marker such as [E3] exactly as is, keep numbers, currency and times unchanged, keep **bold** markers. Return only the translation."""

LANGUAGE_NAMES = {"en": "English", "kn": "Kannada", "hi": "Hindi"}
