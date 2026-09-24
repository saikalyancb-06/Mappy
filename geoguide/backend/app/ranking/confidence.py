"""Confidence and explainability for every recommended place.

* ``confidence`` – how much to trust each kind of information (entity,
  location, hours, price, freshness), with an overall High/Medium/Low label.
* ``conflicts`` – recorded when merged sources disagree (e.g. one says open,
  another says permanently closed), so the answer can say "verify before
  travelling" instead of silently picking one.
* ``bars`` – the 0..1 factor bars behind "Why this place?".
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.core.rules import load_rules
from app.models import Candidate

_LOCATION = {"exact": 0.95, "approximate": 0.7, "dataset": 0.55}
_SOURCE_HOURS = {"curated": 0.85, "database": 0.8, "osm": 0.65, "dataset": 0.55, "serpapi_maps": 0.8}
_SOURCE_PRICE = {"curated": 0.85, "database": 0.8, "dataset": 0.55, "serpapi_hotels": 0.8, "serpapi_maps": 0.6}
_SOURCE_FRESHNESS = {"curated": 0.75, "database": 0.7, "osm": 0.65, "dataset": 0.45}


def _age_days(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400
    except ValueError:
        return None


def _label(value: float) -> str:
    return "high" if value >= 0.75 else "medium" if value >= 0.5 else "low"


def assess(candidate: Candidate) -> dict[str, Any]:
    types = [s.source_type for s in candidate.sources]
    live = [s for s in candidate.sources if s.source_type.startswith("serpapi")]
    conflicts = {c["field"] for c in candidate.conflicts}
    entity = max((s.confidence or 0.5 for s in candidate.sources), default=0.4)
    if len(set(types)) >= 2:
        entity = min(0.99, entity + 0.1)
    location = 0.85 if any(t.startswith("serpapi") for t in types) and candidate.coordinate_precision is None else _LOCATION.get(candidate.coordinate_precision or "", 0.75)
    if "location" in conflicts:
        location = min(location, 0.4)
    if candidate.open_detail.get("provider_text"):
        hours = 0.8
    elif candidate.opening_hours:
        hours = max((_SOURCE_HOURS.get(t, 0.5) for t in types), default=0.5)
    else:
        hours = 0.2
    if "open_status" in conflicts:
        hours = 0.3
    if candidate.kind == "stay":
        price = _SOURCE_PRICE["serpapi_hotels"] if candidate.price_per_night else 0.0
        price_note = f"live rate from {candidate.price_source}" if candidate.price_per_night else "no rate available"
    elif candidate.entry_cost is not None:
        price = max((_SOURCE_PRICE.get(t, 0.5) for t in types), default=0.5)
        price_note = "from " + ("organiser dataset (synthetic)" if types == ["dataset"] else "stored place data")
    else:
        price, price_note = 0.2, "entry cost not verified"
    if "price" in conflicts:
        price = min(price, 0.4)
    ages = [a for a in (_age_days(s.retrieved_at) for s in live) if a is not None]
    if ages:
        freshness = 0.95 if min(ages) < 1 else 0.8 if min(ages) < 30 else 0.5
    else:
        freshness = max((_SOURCE_FRESHNESS.get(t, 0.5) for t in types), default=0.5)
    parts = {"entity": entity, "location": location, "hours": hours, "price": price, "freshness": freshness}
    weights = {"entity": 0.3, "location": 0.2, "hours": 0.2, "price": 0.1, "freshness": 0.2}
    overall = sum(parts[k] * weights[k] for k in parts)
    if conflicts:
        overall = min(overall, 0.55)
    notes = {
        "entity": f"{len(set(types))} source type(s): " + ", ".join(sorted(set(types))),
        "location": "sources disagree on the location" if "location" in conflicts else ("approximate map pin" if candidate.coordinate_precision in {"approximate", "dataset"} else "map pin verified"),
        "hours": "sources disagree on whether it is open" if "open_status" in conflicts else ("live open status" if candidate.open_detail.get("provider_text") else "stored opening hours" if candidate.opening_hours else "opening hours unknown"),
        "price": price_note,
        "freshness": "live source today" if ages and min(ages) < 1 else "stored data",
    }
    return {"overall": round(overall, 2), "label": _label(overall), "parts": {k: {"score": round(v, 2), "label": _label(v), "note": notes[k]} for k, v in parts.items()}}


def bars(candidate: Candidate) -> dict[str, float]:
    """Factor bars for 'Why this place?' (0..1, higher is better for the traveller)."""
    rules = load_rules("ranking")["crowd"]
    cost = candidate.cost_for_user
    if cost.get("kind") == "free":
        cost_bar = 1.0
    elif cost.get("share_of_budget_pct") is not None:
        cost_bar = max(0.0, 1 - cost["share_of_budget_pct"] / 100)
    elif candidate.price_level is not None:
        cost_bar = max(0.0, 1 - candidate.price_level / 4)
    else:
        cost_bar = 0.5
    tags = set(candidate.tags)
    if tags & set(rules["quiet_tags"]):
        quiet = 0.9
    elif tags & set(rules["crowded_tags"]):
        quiet = 0.2
    elif candidate.popularity_score is not None:
        quiet = max(0.1, 1 - candidate.popularity_score / 100 * 0.8)
    else:
        quiet = 0.5
    convenience = math.exp(-(candidate.travel_min or 0) / 30) if candidate.travel_min is not None else candidate.scores.get("geographic", 0.5)
    return {
        "distance": round(candidate.scores.get("geographic", 0.5), 2),
        "rating": round(candidate.scores.get("quality", 0.5), 2),
        "cost": round(cost_bar, 2),
        "quietness": round(quiet, 2),
        "convenience": round(convenience, 2),
        "fit": round((candidate.scores.get("relevance", 0.5) + candidate.scores.get("preference", 0.5)) / 2, 2),
    }


def record_conflicts(primary: Candidate, other: Candidate) -> None:
    """Called while merging two records of the same place: remember material disagreements."""
    from app.geo.distance import haversine_km

    def source(c: Candidate) -> str:
        return c.primary_source.source if c.primary_source else "unknown source"

    if primary.lat is not None and other.lat is not None and haversine_km(primary.lat, primary.lon, other.lat, other.lon) > 1.0:
        primary.conflicts.append({"field": "location", "detail": f"{source(primary)} and {source(other)} place it {haversine_km(primary.lat, primary.lon, other.lat, other.lon):.1f} km apart"})
    closed_flags = [c for c in (primary, other) if c.open_detail.get("permanently_closed")]
    if closed_flags and len(closed_flags) == 1:
        primary.conflicts.append({"field": "open_status", "detail": f"{source(closed_flags[0])} reports it permanently closed; other sources do not"})
    elif primary.open_status != "unknown" and other.open_status != "unknown" and primary.open_status != other.open_status:
        primary.conflicts.append({"field": "open_status", "detail": f"{source(primary)} says {primary.open_status}, {source(other)} says {other.open_status}"})
    if primary.entry_cost is not None and other.entry_cost is not None and primary.entry_cost != other.entry_cost:
        primary.conflicts.append({"field": "price", "detail": f"entry {primary.entry_cost} ({source(primary)}) vs {other.entry_cost} ({source(other)})"})
