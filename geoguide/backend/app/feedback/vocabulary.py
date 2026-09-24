"""Vibe and aspect vocabulary: config → tables, lookups, and custom-vibe normalisation.

The controlled vocabulary lives in data/config/vibes.json and is synced into the
``vibes`` / ``aspects`` tables, so new entries need no schema change. Custom vibes
typed under "Other" are normalised, mapped to an existing vibe when they are a
synonym, and otherwise stored as *pending* custom vibes that only take part in
ranking once enough travellers have used them (then they are promoted).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.rules import load_rules
from app.core.text import normalize, word_pattern
from app.db.models import Aspect, Vibe
from app.db.session import SessionLocal


def config() -> dict[str, Any]:
    return load_rules("vibes")


def sync_vocabulary() -> dict[str, int]:
    """Insert or update the controlled vibes and aspects. Custom vibes are left as they are."""
    cfg = config()
    added = {"vibes": 0, "aspects": 0}
    with SessionLocal() as db:
        vibes = {v.key: v for v in db.scalars(select(Vibe)).all()}
        for entry in cfg["vibes"]:
            row = vibes.get(entry["key"])
            if row is None:
                db.add(Vibe(key=entry["key"], label=entry["label"], emoji=entry.get("emoji"), origin="controlled", status="active"))
                added["vibes"] += 1
            else:
                row.label, row.emoji, row.status = entry["label"], entry.get("emoji"), "active" if row.status != "blocked" else row.status
                if row.origin == "custom":
                    row.origin = "controlled"  # a custom vibe that made it into config
        aspects = {a.key: a for a in db.scalars(select(Aspect)).all()}
        for entry in cfg["aspects"]:
            row = aspects.get(entry["key"])
            if row is None:
                db.add(Aspect(key=entry["key"], label=entry["label"], polarity=entry["polarity"], origin="controlled", status="active"))
                added["aspects"] += 1
            else:
                row.label, row.polarity = entry["label"], entry["polarity"]
        db.commit()
    invalidate_vocabulary()
    return added


@dataclass
class Vocabulary:
    vibes: dict[str, Vibe]  # key → row (active + pending custom)
    aspects: dict[str, Aspect]

    @property
    def active_vibes(self) -> dict[str, Vibe]:
        return {key: vibe for key, vibe in self.vibes.items() if vibe.status == "active"}

    def vibe_by_id(self) -> dict[int, Vibe]:
        return {vibe.id: vibe for vibe in self.vibes.values()}

    def aspect_by_id(self) -> dict[int, Aspect]:
        return {aspect.id: aspect for aspect in self.aspects.values()}


_cache: dict[str, Any] = {"at": 0.0, "vocab": None}
_TTL_S = 60.0


def invalidate_vocabulary() -> None:
    _cache["vocab"] = None


def load_vocabulary() -> Vocabulary:
    import time

    if _cache["vocab"] is not None and time.monotonic() - _cache["at"] < _TTL_S:
        return _cache["vocab"]
    with SessionLocal() as db:
        vibes = {v.key: v for v in db.scalars(select(Vibe).where(Vibe.status != "blocked")).all()}
        aspects = {a.key: a for a in db.scalars(select(Aspect).where(Aspect.status != "blocked")).all()}
        for row in [*vibes.values(), *aspects.values()]:
            db.expunge(row)
    if not vibes:
        sync_vocabulary()
        return load_vocabulary()
    _cache.update(at=time.monotonic(), vocab=Vocabulary(vibes, aspects))
    return _cache["vocab"]


def public_vocabulary() -> dict[str, Any]:
    """What the feedback UI shows: controlled vibes, promoted custom vibes, aspects and scales."""
    cfg = config()
    vocab = load_vocabulary()
    order = {entry["key"]: index for index, entry in enumerate(cfg["vibes"])}
    vibes = sorted((v for v in vocab.active_vibes.values()), key=lambda v: (order.get(v.key, 999), v.label))
    return {
        "vibes": [{"key": v.key, "label": v.label, "emoji": v.emoji, "origin": v.origin} for v in vibes],
        "liked": [{"key": a.key, "label": a.label} for a in vocab.aspects.values() if a.polarity == "positive" and a.status == "active"],
        "disliked": [{"key": a.key, "label": a.label} for a in vocab.aspects.values() if a.polarity == "negative" and a.status == "active"],
        "ratings": [{"value": int(value), "recommendation": key, "label": cfg["recommendation_labels"][key]} for value, key in sorted(cfg["recommendation"].items(), reverse=True)],
        "crowd_levels": cfg["crowd_levels"],
        "price_levels": cfg["price_levels"],
        "score_fields": cfg["score_fields"],
        "custom_vibe_max_length": cfg["custom_vibes"]["max_length"],
    }


class InvalidCustomVibe(ValueError):
    pass


def vibe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize(text)).strip("_")


def normalize_custom_vibe(text: str) -> tuple[str, str]:
    """Clean a typed vibe. Returns (key, label); a synonym of a controlled vibe returns that vibe's key.

    Rejects empty, over-long, digit/symbol-heavy or blocked input so malformed text can't enter ranking.
    """
    rules = config()["custom_vibes"]
    cleaned = " ".join(re.sub(r"[^\w\s'&-]", " ", str(text or "")).split())
    if len(cleaned) > rules["max_length"]:
        raise InvalidCustomVibe(f"Keep a custom vibe under {rules['max_length']} characters.")
    letters = sum(ch.isalpha() for ch in cleaned)
    if letters < rules["min_letters"] or any(ch.isdigit() for ch in cleaned):
        raise InvalidCustomVibe("A custom vibe should be a word or two, like 'artsy' or 'spiritual'.")
    norm = normalize(cleaned)
    if any(word_pattern(word).search(norm) for word in rules["blocked_words"]):
        raise InvalidCustomVibe("That vibe can't be used.")
    synonyms = {normalize(k): v for k, v in config()["vibe_synonyms"].items()}
    controlled = {normalize(entry["label"]): entry["key"] for entry in config()["vibes"]} | {normalize(entry["key"].replace("_", " ")): entry["key"] for entry in config()["vibes"]}
    if norm in controlled:
        return controlled[norm], cleaned
    if norm in synonyms:
        return synonyms[norm], cleaned
    return vibe_key(cleaned), cleaned.title()


def ensure_custom_vibe(db, key: str, label: str) -> Vibe:
    """Get or create a custom vibe; count the use and promote it once enough people used it."""
    row = db.scalars(select(Vibe).where(Vibe.key == key)).first()
    if row is None:
        row = Vibe(key=key, label=label[:40], origin="custom", status="pending", use_count=0)
        db.add(row)
        db.flush()
    row.use_count = (row.use_count or 0) + 1
    if row.origin == "custom" and row.status == "pending" and row.use_count >= config()["custom_vibes"]["promote_after_uses"]:
        row.status = "active"
    invalidate_vocabulary()
    return row
