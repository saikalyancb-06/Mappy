"""Candidate aggregation: merge sources, deduplicate, keep provenance.

The same place can arrive from the curated database, OSM and Google Maps.
Duplicates are merged into one canonical candidate; field values are chosen by
configurable per-field source priority and every source is retained.
"""
from __future__ import annotations

from dataclasses import fields
from typing import Iterable
from urllib.parse import urlparse

from app.core.rules import load_rules
from app.core.text import distinctive_tokens, name_similarity, normalize
from app.geo.distance import haversine_km
from app.models import Candidate
from app.ranking.confidence import record_conflicts

_MERGEABLE = [f.name for f in fields(Candidate) if f.name not in {"id", "sources", "confidence", "distance_km", "scores", "score", "reasons", "facts", "open_detail", "tags", "conflicts", "confidence_detail", "bars", "cost_for_user"}]


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or None


def _digits(phone: str | None) -> str | None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 8 else None


def same_entity(a: Candidate, b: Candidate) -> bool:
    rules = load_rules("ranking")["dedup"]
    source_ids_a = {s.source_id for s in a.sources if s.source_id}
    if source_ids_a & {s.source_id for s in b.sources if s.source_id} or a.id == b.id:
        return True
    # Numbered names ("Terminal 1" / "Terminal 2", "Museum 2") are different places however similar they look.
    if {t for t in normalize(a.name).split() if any(ch.isdigit() for ch in t)} != {t for t in normalize(b.name).split() if any(ch.isdigit() for ch in t)}:
        return False
    # Within one destination, an identical name is the same place even when sources disagree on coordinates.
    if a.destination_id and a.destination_id == b.destination_id and normalize(a.name) == normalize(b.name):
        return True
    if a.lat is None or b.lat is None:
        return False
    distance = haversine_km(a.lat, a.lon, b.lat, b.lon)
    similarity = name_similarity(a.name, b.name)
    if similarity >= rules["name_similarity"] and distance <= rules["max_distance_km"]:
        return True
    ta, tb = distinctive_tokens(a.name), distinctive_tokens(b.name)
    if ta and ta == tb and distance <= rules["max_distance_km"]:
        return True
    contact_match = (_digits(a.phone) and _digits(a.phone) == _digits(b.phone)) or (_domain(a.website) and _domain(a.website) == _domain(b.website))
    return bool(contact_match) and distance <= rules["same_contact_distance_km"]


def _priority(field_name: str) -> list[str]:
    priorities = load_rules("ranking")["source_priority"]
    return priorities.get(field_name) or priorities["default"]


def _rank(candidate: Candidate) -> int:
    order = _priority("default")
    return min((order.index(s.source_type) for s in candidate.sources if s.source_type in order), default=len(order))


def merge(primary: Candidate, other: Candidate) -> Candidate:
    """Merge ``other`` into ``primary`` field by field using source priority."""
    record_conflicts(primary, other)
    primary.conflicts.extend(c for c in other.conflicts if c not in primary.conflicts)
    for name in _MERGEABLE:
        mine, theirs = getattr(primary, name), getattr(other, name)
        if theirs in (None, "", [], "unknown"):
            continue
        if mine in (None, "", [], "unknown"):
            setattr(primary, name, theirs)
            continue
        order = _priority("coordinates" if name in {"lat", "lon"} else name)
        rank_mine = min((order.index(s.source_type) for s in primary.sources if s.source_type in order), default=len(order))
        rank_theirs = min((order.index(s.source_type) for s in other.sources if s.source_type in order), default=len(order))
        if rank_theirs < rank_mine:
            setattr(primary, name, theirs)
    if other.open_detail and (primary.open_status == other.open_status):
        primary.open_detail = {**other.open_detail, **primary.open_detail}
    primary.tags = sorted(set(primary.tags) | set(other.tags))
    known = {(s.source_type, s.source_id, s.source) for s in primary.sources}
    for source in other.sources:
        if (source.source_type, source.source_id, source.source) not in known:
            primary.sources.append(source)
    # Independent agreement raises confidence: 1 - Π(1 - c_i)
    remaining = 1.0
    for source in primary.sources:
        remaining *= 1.0 - float(source.confidence or 0.5)
    primary.confidence = round(min(0.99, 1.0 - remaining), 3)
    if primary.distance_km is None:
        primary.distance_km = other.distance_km
    return primary


def aggregate(groups: Iterable[list[Candidate]]) -> tuple[list[Candidate], int]:
    """Return (deduplicated candidates, number of duplicates merged). Earlier groups are preferred as primaries."""
    merged: list[Candidate] = []
    duplicates = 0
    for group in groups:
        for candidate in group:
            index = next((i for i, existing in enumerate(merged) if same_entity(existing, candidate)), None)
            if index is None:
                merged.append(candidate)
            else:
                # The more trusted source becomes the canonical record (its id, name and coordinates).
                if _rank(candidate) < _rank(merged[index]):
                    merged[index] = merge(candidate, merged[index])
                else:
                    merge(merged[index], candidate)
                duplicates += 1
    return merged, duplicates


def normalized_key(candidate: Candidate) -> str:
    return normalize(candidate.name)
