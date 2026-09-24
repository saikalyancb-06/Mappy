"""Text normalisation and similarity used by aliasing, dedup and entity resolution."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_TOKEN = re.compile(r"[a-z0-9]+")
# Words that describe *what* a place is rather than *which* place it is.
GENERIC_PLACE_WORDS = {
    "the", "a", "an", "of", "and", "at", "in", "near", "sri", "shri", "new", "old",
    "restaurant", "hotel", "cafe", "coffee", "temple", "resort", "inn", "lodge", "house",
    "guest", "guesthouse", "homestay", "bar", "bakery", "shop", "store", "center", "centre",
}


def normalize(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    ascii_text = ascii_text.replace("&", " and ").replace("'", "")
    return " ".join(_TOKEN.findall(ascii_text))


def tokens(text: str | None) -> list[str]:
    return normalize(text).split()


def distinctive_tokens(text: str | None) -> set[str]:
    return {token for token in tokens(text) if token not in GENERIC_PLACE_WORDS}


def token_set_ratio(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def name_similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity robust to word order, punctuation and minor spelling differences."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sequence = SequenceMatcher(None, na, nb).ratio()
    jaccard = token_set_ratio(na, nb)
    ta, tb = set(na.split()), set(nb.split())
    containment = len(ta & tb) / min(len(ta), len(tb))
    # A mention fully contained in a longer name ("vittala" in "vittala temple complex")
    # is strong evidence, but less than an exact match.
    contained = 0.9 * containment if containment == 1.0 else 0.0
    return max(sequence, jaccard, contained)
