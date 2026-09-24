"""Answer validation: catches the LLM overriding retrieved facts.

Checks citations, named places, ratings, distances, prices, clock times,
temperatures and "open now" claims against the evidence. Issues trigger a
repair attempt and, failing that, the deterministic grounded answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.text import name_similarity, normalize
from app.models import Evidence

_CITATION = re.compile(r"\[(E\d+)\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_RATING = re.compile(r"(?:rated|rating(?:\s+of)?)\s*(\d(?:\.\d)?)|(\d\.\d)\s*(?:★|stars?|/\s*5)", re.IGNORECASE)
_DISTANCE = re.compile(r"(\d+(?:\.\d+)?)\s*(km|kilometres|kilometers|m|metres|meters)\b", re.IGNORECASE)
_PRICE = re.compile(r"(?:₹|rs\.?|inr)\s?(\d[\d,]*)", re.IGNORECASE)
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_TEMP = re.compile(r"(-?\d+(?:\.\d+)?)\s*°\s*c?", re.IGNORECASE)


@dataclass
class ValidationResult:
    status: str  # ok | issues
    issues: list[dict[str, Any]] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    uncited_sentences: int = 0
    sentences: int = 0

    @property
    def evidence_coverage(self) -> float:
        return round(1 - self.uncited_sentences / self.sentences, 3) if self.sentences else 1.0

    def feedback(self) -> str:
        return "\n".join(f"- {issue['type']}: {issue['detail']}" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "issues": self.issues, "citations": self.citations, "evidence_coverage": self.evidence_coverage}


def _numbers(evidence: list[Evidence]) -> tuple[str, set[float], set[float], set[float]]:
    text = " ".join(f"{e.title} {e.content}" for e in evidence)
    ratings = {float(e.metadata["rating"]) for e in evidence if e.metadata.get("rating") is not None}
    distances: set[float] = set()
    for e in evidence:
        for key in ("distance_km", "distance_from_user_km"):
            if e.metadata.get(key) is not None:
                distances.add(float(e.metadata[key]))
    for value, unit in _DISTANCE.findall(text):
        distances.add(float(value) / 1000 if unit.lower().startswith("m") and not unit.lower().startswith("mi") else float(value))
    temps = {float(v) for v in _TEMP.findall(text)}
    return text, ratings, distances, temps


def validate(answer: str, evidence: list[Evidence], *, known_names: list[str] | None = None) -> ValidationResult:
    result = ValidationResult(status="ok")
    ids = {item.id for item in evidence}
    cited = _CITATION.findall(answer)
    result.citations = sorted(set(cited), key=lambda x: int(x[1:]))
    for citation in set(cited) - ids:
        result.issues.append({"type": "invalid_citation", "detail": f"{citation} is not in the evidence"})

    names = [e.title for e in evidence] + list(known_names or [])
    for bold in _BOLD.findall(answer):
        label = bold.strip().rstrip(":")
        if len(normalize(label)) < 3:
            continue
        if not any(name_similarity(label, name) >= 0.75 or normalize(label) in normalize(name) or normalize(name) in normalize(label) for name in names):
            result.issues.append({"type": "unsupported_place", "detail": f"'{label}' is not a place in the evidence"})

    text, ratings, distances, temps = _numbers(evidence)
    evidence_text = text.lower()
    for match in _RATING.finditer(answer):
        value = float(match.group(1) or match.group(2))
        if not any(abs(value - r) <= 0.05 for r in ratings):
            result.issues.append({"type": "invented_rating", "detail": f"rating {value} is not in the evidence"})
    for value, unit in _DISTANCE.findall(answer):
        km = float(value) / 1000 if unit.lower() in {"m", "metres", "meters"} else float(value)
        if not any(abs(km - d) <= max(0.15, 0.15 * d) for d in distances):
            result.issues.append({"type": "invented_distance", "detail": f"distance {value} {unit} is not supported"})
    for amount in _PRICE.findall(answer):
        if amount.replace(",", "") not in evidence_text.replace(",", ""):
            result.issues.append({"type": "invented_price", "detail": f"price {amount} is not in the evidence"})
    for hour, minute in _TIME.findall(answer):
        stamp = f"{int(hour):02d}:{minute}"
        if stamp not in evidence_text and f"{int(hour)}:{minute}" not in evidence_text:
            result.issues.append({"type": "invented_time", "detail": f"time {stamp} is not in the evidence"})
    for value in _TEMP.findall(answer):
        if not any(abs(float(value) - t) <= 1.5 for t in temps):
            result.issues.append({"type": "invented_temperature", "detail": f"temperature {value}° is not in the evidence"})
    if re.search(r"\bopen (?:right )?now\b", answer, re.IGNORECASE) and not any(e.metadata.get("open_status") == "open" for e in evidence):
        result.issues.append({"type": "invented_open_status", "detail": "no evidence says a place is open now"})

    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", answer) if len(normalize(s).split()) >= 6]
    result.sentences = len(sentences)
    result.uncited_sentences = sum(1 for s in sentences if not _CITATION.search(s))
    if result.issues:
        result.status = "issues"
    return result
