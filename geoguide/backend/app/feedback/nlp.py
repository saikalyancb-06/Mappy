"""Lightweight, deterministic text analysis for "Tell us more".

Lexicon-based (vocabulary in data/config/vibes.json) with negation handling, so it
runs offline and is explainable. Its output is stored as *derived* hints next to
the traveller's original words; it never overrides what they selected.
"""
from __future__ import annotations

import re
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.text import normalize
from app.feedback.vocabulary import config

_INTENSIFIERS = {"very", "so", "really", "too", "quite", "super", "pretty", "extremely", "bit", "little"}
_INTENSIFIER = re.compile(r"(?=\b(?:" + "|".join(sorted(_INTENSIFIERS)) + r")\s+([a-z]{3,})\b)")
_STOP = {"much", "many", "good", "nice", "great", "bad", "the", "and", "for", "with", "very", "place", "time"}


@dataclass
class TextSignals:
    sentiment: str = "neutral"  # positive | neutral | mixed | negative
    sentiment_score: float = 0.0  # -1..1
    sentiment_source: str = "rating"  # text | rating
    liked_aspects: list[str] = field(default_factory=list)
    disliked_aspects: list[str] = field(default_factory=list)
    vibes: list[str] = field(default_factory=list)
    descriptors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _occurrences(norm: str, phrase: str) -> list[int]:
    target = normalize(phrase)
    if not target:
        return []
    return [m.start() for m in _occurrence_pattern(target).finditer(norm)]


@lru_cache(maxsize=4096)
def _occurrence_pattern(target: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|(?<=\s)){re.escape(target)}(?=\s|$)")


def _negated(norm: str, position: int, negations: set[str]) -> bool:
    before = norm[:position].split()[-3:]
    return any(word in negations for word in before)


def analyse(text: str | None, rating: int | None = None) -> TextSignals:
    cfg = config()
    signals = TextSignals()
    norm = normalize(text or "")
    negations = {normalize(n) for n in cfg["sentiment"]["negations"]}
    if norm:
        pos = neg = 0
        for word in cfg["sentiment"]["positive"]:
            for position in _occurrences(norm, word):
                if _negated(norm, position, negations):
                    neg += 1
                else:
                    pos += 1
        for word in cfg["sentiment"]["negative"]:
            for position in _occurrences(norm, word):
                if _negated(norm, position, negations):
                    pos += 1
                else:
                    neg += 1
        if pos or neg:
            score = (pos - neg) / (pos + neg)
            signals.sentiment_score, signals.sentiment_source = round(score, 3), "text"
            signals.sentiment = "mixed" if pos and neg and abs(score) < 0.5 else "positive" if score > 0.2 else "negative" if score < -0.2 else "neutral"
        taken: list[tuple[int, int]] = []
        for entry in sorted(cfg["aspects"], key=lambda a: -max((len(k) for k in a["keywords"]), default=0)):
            for keyword in sorted(entry["keywords"], key=len, reverse=True):
                hits = [p for p in _occurrences(norm, keyword) if not _negated(norm, p, negations) and not any(s <= p < e for s, e in taken)]
                if hits:
                    taken.extend((p, p + len(normalize(keyword))) for p in hits)
                    (signals.liked_aspects if entry["polarity"] == "positive" else signals.disliked_aspects).append(entry["key"])
                    break
        for entry in cfg["vibes"]:
            if any(not _negated(norm, p, negations) for keyword in entry["keywords"] for p in _occurrences(norm, keyword)):
                signals.vibes.append(entry["key"])
        known = {normalize(k) for entry in [*cfg["vibes"], *cfg["aspects"]] for k in entry["keywords"]} | _INTENSIFIERS
        signals.descriptors = list(dict.fromkeys(word for word in _INTENSIFIER.findall(norm) if word not in _STOP and word not in known))[:5]
    if signals.sentiment_source == "rating" and rating is not None:
        signals.sentiment = "positive" if rating >= 4 else "negative" if rating <= 2 else "neutral"
        signals.sentiment_score = round((rating - 3) / 2, 3)
    return signals
