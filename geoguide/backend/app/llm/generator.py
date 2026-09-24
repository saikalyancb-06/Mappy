"""Grounded answer generation with validation, one repair pass and a
deterministic evidence-only fallback (used when the LLM is unavailable or
keeps contradicting the evidence)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import Trace
from app.llm.client import LLMClient, LLMUnavailable, llm as default_llm
from app.llm.prompts import LANGUAGE_NAMES, TRANSLATE_PROMPT, AnswerContext, build_messages
from app.llm.validator import ValidationResult, validate
from app.models import Evidence


@dataclass
class GeneratedAnswer:
    text: str
    mode: str  # llm | llm_repaired | deterministic
    validation: ValidationResult | None
    language: str = "en"
    translated: bool = False
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "language": self.language, "translated": self.translated, "validation": self.validation.as_dict() if self.validation else None, "errors": self.errors}


def _first_sentence(text: str, limit: int = 220) -> str:
    sentence = text.split(". ")[0].strip()
    return (sentence[:limit] + "…") if len(sentence) > limit else sentence.rstrip(".") + "."


def deterministic_answer(context: AnswerContext) -> str:
    """Compose an answer purely from evidence, with citations. No model involved."""
    by_type: dict[str, list[Evidence]] = {}
    for item in context.evidence:
        by_type.setdefault(item.source_type, []).append(item)
    if not context.evidence:
        return context.extra.get("empty_message") or "I couldn't find verified information for that yet."
    lines: list[str] = []
    lead = context.extra.get("lead")
    if lead:
        lines.append(lead)
    weather = by_type.get("weather", [])
    if weather and context.intent in {"WEATHER", "ITINERARY", "ACTIVITY_DISCOVERY", "SAFETY"}:
        lines.append(f"{weather[0].content} [{weather[0].id}]")
    pois = by_type.get("poi", [])
    if pois:
        if context.intent != "WEATHER":
            for item in pois[:5]:
                details = [part.strip() for part in item.content.split(";")[1:]]
                keep = [d for d in details if d.startswith(("distance", "currently", "entry", "typical visit", "rating", "area"))][:4]
                lines.append(f"- **{item.title}** — {', '.join(keep) if keep else _first_sentence(item.content)} [{item.id}]")
    knowledge = by_type.get("knowledge", [])
    if knowledge and context.intent in {"DESTINATION_KNOWLEDGE", "PLACE_LOOKUP", "GENERAL_TRAVEL_QUESTION", "ACTIVITY_DISCOVERY"}:
        limit = 3 if context.intent in {"DESTINATION_KNOWLEDGE", "GENERAL_TRAVEL_QUESTION", "PLACE_LOOKUP"} else 1
        for item in knowledge[:limit]:
            lines.append(f"{_first_sentence(item.content, 320)} [{item.id}]")
    for item in by_type.get("safety", [])[:3]:
        lines.append(f"⚠ {item.title} ({item.metadata.get('severity')}): {item.metadata.get('body') or item.content} [{item.id}]")
    for item in by_type.get("event", [])[:3]:
        lines.append(f"- {item.content} [{item.id}]")
    for item in by_type.get("web", [])[:3]:
        lines.append(f"- {item.title}: {_first_sentence(item.content)} [{item.id}]")
    if not lines:
        return context.extra.get("empty_message") or "I couldn't find verified information for that yet."
    for note in context.extra.get("closing_notes", []):
        lines.append(note)
    return "\n".join(lines)


def translate(text: str, language: str, client: LLMClient) -> tuple[str, bool, dict[str, str] | None]:
    if language == "en" or language not in LANGUAGE_NAMES:
        return text, False, None
    try:
        translated = client.chat([{"role": "system", "content": TRANSLATE_PROMPT.format(language=LANGUAGE_NAMES[language])}, {"role": "user", "content": text}], model=client.reasoning_model, temperature=0.1, max_tokens=1200)
        return translated, True, None
    except LLMUnavailable as exc:
        return text, False, exc.as_dict()


def generate(context: AnswerContext, *, language: str = "en", client: LLMClient | None = None, trace: Trace | None = None, known_names: list[str] | None = None) -> GeneratedAnswer:
    client = client or default_llm
    errors: list[dict[str, str]] = []
    answer: GeneratedAnswer | None = None
    if context.evidence and client.configured:
        feedback = None
        for attempt in range(2):
            try:
                if trace:
                    trace.start_timer(f"llm_attempt_{attempt + 1}")
                text = client.chat(build_messages(context, feedback), max_tokens=700)
                if trace:
                    trace.stop_timer(f"llm_attempt_{attempt + 1}")
            except LLMUnavailable as exc:
                errors.append(exc.as_dict())
                break
            validation = validate(text, context.evidence, known_names=known_names)
            if trace:
                trace.step("answer_validation", attempt=attempt + 1, status=validation.status, issues=validation.issues, coverage=validation.evidence_coverage)
            if validation.status == "ok":
                answer = GeneratedAnswer(text, "llm" if attempt == 0 else "llm_repaired", validation, errors=errors)
                break
            feedback = validation.feedback()
    if answer is None:
        text = deterministic_answer(context)
        answer = GeneratedAnswer(text, "deterministic", validate(text, context.evidence, known_names=known_names), errors=errors)
    if language != "en":
        translated, done, error = translate(answer.text, language, client)
        answer.text, answer.translated, answer.language = translated, done, language if done else "en"
        if error:
            answer.errors.append(error)
    return answer
