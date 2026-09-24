"""Event category taxonomy (data/config/events.json) and price classification."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.core.rules import load_rules
from app.core.text import normalize


def _has(text: str, phrase: str) -> bool:
    target = normalize(phrase)
    return bool(target) and f" {target} " in f" {text} "


def categorise(title: str, description: str | None = None, provider: str | None = None, hints: list[str] | None = None) -> tuple[str, list[str], str]:
    """(primary category, all matching categories, type). Provider categories count first, then the title, then the description."""
    rules = load_rules("events")
    found: list[str] = []
    mapping = rules["provider_categories"].get(provider or "", {})
    for hint in hints or []:
        mapped = mapping.get(normalize(hint)) or mapping.get(str(hint).lower())
        if mapped and mapped not in found:
            found.append(mapped)
    title_norm, text_norm = normalize(title), normalize(f"{title} {description or ''}")
    for scope in (title_norm, text_norm):
        for category in rules["category_priority"]:
            if category not in found and any(_has(scope, word) for word in rules["categories"][category]["keywords"]):
                found.append(category)
    festival = any(_has(title_norm, word) for word in rules["festival_keywords"])
    if festival and "festivals" not in found:
        found.insert(0, "festivals")
    categories = [c for c in found if c != "other"] or ["other"]  # a provider's "other" never outranks a real match
    primary = categories[0]
    kind = "festival" if festival or primary in rules["festival_categories"] and not any(c in categories for c in ("music", "art", "food", "comedy")) else "event"
    return primary, categories, kind


_AMOUNT = re.compile(r"(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:rupees|inr|usd|eur)", re.IGNORECASE)
_CURRENCY = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR", "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}


def price_from_text(text: str | None) -> tuple[str, str | None, str | None]:
    """(kind, minimum as decimal text, currency) from free text; 'unknown' unless the text says so."""
    lowered = (text or "").lower()
    if re.search(r"\b(?:donation|pay what you (?:want|can)|by donation|suggested contribution)\b", lowered):
        return "donation", None, None
    if re.search(r"\bfree\s+(?:entry|admission|event|for all|of (?:charge|cost))\b|\bentry\s+(?:is\s+)?free\b|\bno (?:entry|admission) (?:fee|charge)\b|\badmission\s+(?:is\s+)?free\b", lowered):
        return "free", "0.00", None
    match = _AMOUNT.search(text or "")
    if match:
        raw = (match.group(1) or match.group(2) or "").replace(",", "")
        symbol = re.match(r"(₹|rs\.?|inr|\$|usd|€|eur|£|gbp|rupees)", match.group(0).strip().lower() + " ")
        try:
            amount = Decimal(raw).quantize(Decimal("0.01"))
        except InvalidOperation:
            return "unknown", None, None
        currency = _CURRENCY.get(symbol.group(1) if symbol else "", None) or next((v for k, v in _CURRENCY.items() if k in match.group(0).lower()), None)
        return ("free" if amount == 0 else "paid"), str(amount), currency
    return "unknown", None, None
