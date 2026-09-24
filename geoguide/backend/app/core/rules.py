"""Loads the JSON rule/weight files in data/config (taxonomy, intents, ranking, itinerary)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.config import CONFIG_DIR


@lru_cache(maxsize=None)
def load_rules(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def taxonomy() -> dict[str, Any]:
    return load_rules("taxonomy")


def category_kind(category: str | None) -> str | None:
    if not category:
        return None
    entry = taxonomy()["categories"].get(category)
    return entry.get("kind") if entry else None


def group_categories(group: str) -> list[str]:
    entry = taxonomy()["groups"].get(group)
    return list(entry["categories"]) if entry else []


def category_group(category: str | None) -> str | None:
    if not category:
        return None
    entry = taxonomy()["categories"].get(category)
    return entry.get("group") if entry else None


def categories_for_interest(interest: str) -> set[str]:
    """Categories a user interest (a taxonomy group or category id) maps to."""
    interest = (interest or "").strip().lower()
    cats = set(group_categories(interest))
    if interest in taxonomy()["categories"]:
        cats.add(interest)
    return cats
