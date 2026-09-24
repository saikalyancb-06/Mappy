"""Okapi BM25 lexical retrieval (the non-semantic half of hybrid search)."""
from __future__ import annotations

import math
from collections import Counter

from app.core.text import tokens

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by", "is", "are", "was", "were", "be", "it",
    "this", "that", "these", "those", "what", "which", "who", "whom", "why", "how", "when", "where", "i", "me", "my", "we",
    "you", "your", "can", "could", "should", "would", "do", "does", "did", "there", "here", "about", "tell", "please", "any",
    "some", "from", "as", "its", "into", "than", "then", "so", "if", "also", "just", "get", "go", "visit", "place", "places",
}


def analyze(text: str | None) -> list[str]:
    out = []
    for token in tokens(text):
        if token in STOPWORDS or len(token) < 2:
            continue
        # light plural folding so "temples" matches "temple"
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.append(token)
    return out


def bm25_scores(query: str, documents: list[tuple[str, str]], k1: float = 1.4, b: float = 0.75) -> dict[str, float]:
    """documents: [(doc_id, text)] -> {doc_id: score} (only positive scores)."""
    query_terms = analyze(query)
    if not query_terms or not documents:
        return {}
    analyzed = [(doc_id, analyze(text)) for doc_id, text in documents]
    avg_len = sum(len(terms) for _, terms in analyzed) / max(len(analyzed), 1)
    df: Counter[str] = Counter()
    for _, terms in analyzed:
        df.update(set(terms))
    n = len(analyzed)
    scores: dict[str, float] = {}
    for doc_id, terms in analyzed:
        tf = Counter(terms)
        length = len(terms) or 1
        score = 0.0
        for term in set(query_terms):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (tf[term] * (k1 + 1)) / (tf[term] + k1 * (1 - b + b * length / (avg_len or 1)))
        if score > 0:
            scores[doc_id] = score
    return scores
