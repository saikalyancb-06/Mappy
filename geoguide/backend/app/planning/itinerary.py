"""Constraint-aware itinerary optimiser.

Greedy best-insertion over ranked candidates with hard constraints (time
window, opening hours for the whole visit, locked stops) and a weighted
objective (value, time, cost, carbon, walking). Re-plan presets change the
weights and walking limits; the plan explains what changed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.rules import load_rules
from app.geo import opening_hours
from app.geo.distance import haversine_km
from app.models import Candidate
from app.services.cost import money_text, sum_money, to_decimal


@dataclass
class PlanRequest:
    candidates: list[Candidate]
    start_point: tuple[float, float]
    start_label: str
    start_time: datetime  # timezone-aware local time
    duration_min: int
    preset: str = "balanced"
    walking: str = "moderate"
    currency: str | None = None
    locked_ids: list[str] = field(default_factory=list)
    excluded_ids: list[str] = field(default_factory=list)
    weather: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    budget_cap: Decimal | None = None  # hard: the plan's known costs must fit
    travel_mode: str | None = None  # traveller's own transport for longer legs
    end_label: str | None = None
    understood: list[str] = field(default_factory=list)
    not_before: dict[str, datetime] = field(default_factory=dict)  # e.g. a sunset spot is reached shortly before sunset
    timing_notes: dict[str, str] = field(default_factory=dict)


@dataclass
class _Leg:
    mode: str
    distance_km: float
    minutes: int
    cost: float | None
    co2_g: float


def _leg(a: tuple[float, float], b: tuple[float, float], request: PlanRequest) -> _Leg:
    config = load_rules("itinerary")
    preset = config["presets"].get(request.preset) or config["presets"]["balanced"]
    straight = haversine_km(a[0], a[1], b[0], b[1])
    distance = straight * config["detour_factor"]
    walk_limit = config["walk_max_km"].get(request.walking, 1.5) * preset["walk_multiplier"]
    own = {"auto": "auto_rickshaw"}.get(request.travel_mode or "", request.travel_mode)
    if request.travel_mode == "walk":
        mode = "walk"
    elif distance <= walk_limit and own not in {"car", "motorbike"}:
        mode = "walk"
    elif own in config["modes"]:
        mode = own
    elif preset.get("bicycle_max_km") and distance <= preset["bicycle_max_km"]:
        mode = "bicycle"
    else:
        mode = "auto_rickshaw"
    spec = config["modes"][mode]
    minutes = max(2, int(round(distance / spec["speed_kmh"] * 60 + spec.get("wait_min", 0))))
    costs = (config.get("transport_costs") or {}).get(request.currency or "")
    cost = None
    if costs and mode in costs:
        cost = 0.0 if distance < 0.05 else round(costs[mode]["base"] + costs[mode]["per_km"] * distance, 0)
    return _Leg(mode, round(distance, 2), minutes, cost, round(distance * spec["co2_g_per_km"], 0))


def _visit_minutes(candidate: Candidate) -> int:
    defaults = load_rules("itinerary")["default_visit_min"]
    return int(candidate.visit_duration_min or defaults.get(candidate.category or "", defaults["default"]))


def _weather_factor(candidate: Candidate, when: datetime, request: PlanRequest) -> tuple[float, str | None]:
    weather = request.weather or {}
    if weather.get("status") != "ok" or candidate.indoor:
        return 1.0, None
    config = load_rules("itinerary")["weather"]
    stamp = when.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    hour = next((h for h in weather.get("hourly", []) if h["time"] == stamp), None)
    if hour:
        if hour.get("precipitation_probability") is not None and hour["precipitation_probability"] >= config["rain_probability"]:
            return config["rain_outdoor_value_factor"], "rain likely at this hour"
        if hour.get("apparent_c") is not None and hour["apparent_c"] >= config["heat_apparent_c"] and when.hour in config["heat_hours"]:
            return config["heat_outdoor_value_factor"], "peak heat at this hour"
    return 1.0, None


def _simulate(order: list[Candidate], request: PlanRequest) -> dict[str, Any] | None:
    """Schedule stops in order; return None if any hard constraint fails."""
    config = load_rules("itinerary")
    end_limit = request.start_time + timedelta(minutes=request.duration_min)
    clock = request.start_time
    position = request.start_point
    stops = []
    totals = {"travel_min": 0, "visit_min": 0, "wait_min": 0, "cost": 0.0, "cost_unknown_items": 0, "co2_g": 0.0, "walking_km": 0.0, "value": 0.0}
    for candidate in order:
        leg = _leg(position, (candidate.lat, candidate.lon), request)
        arrive = clock + timedelta(minutes=leg.minutes + (config["buffer_min"] if stops else 0))
        visit = _visit_minutes(candidate)
        wait = 0
        free = 0
        target = request.not_before.get(candidate.id)
        if target and arrive < target:
            free = int((target - arrive).total_seconds() // 60)  # chosen free time, not a wait for opening
            arrive = target
        hours = candidate.opening_hours
        open_check = "hours_unknown"
        if hours:
            status = opening_hours.status_at(hours, arrive)
            if status["status"] == "closed" and status.get("opens_at") and ":" in status["opens_at"] and " " not in status["opens_at"]:
                opens = datetime.strptime(status["opens_at"], "%H:%M").time()
                opens_dt = arrive.replace(hour=opens.hour, minute=opens.minute, second=0, microsecond=0)
                wait = int((opens_dt - arrive).total_seconds() // 60)
                if wait > (config["max_first_wait_min"] if not stops else config["max_wait_for_opening_min"]):
                    return None  # the first stop may start the day a little later, at its opening time
                arrive = opens_dt
            ok = opening_hours.open_for_window(hours, arrive, arrive + timedelta(minutes=visit))
            if ok is False:
                return None
            open_check = "verified_open" if ok else "hours_unknown"
        depart = arrive + timedelta(minutes=visit)
        if depart > end_limit:
            return None
        if request.budget_cap is not None:
            known = Decimal(str(totals["cost"])) + (to_decimal(leg.cost) or Decimal(0)) + (to_decimal(candidate.entry_cost) or Decimal(0))
            if known > request.budget_cap:
                return None
        factor, weather_note = _weather_factor(candidate, arrive, request)
        value = (candidate.score or 0.3) * factor
        entry_cost = float(to_decimal(candidate.entry_cost)) if candidate.entry_cost is not None else candidate.entry_fee
        totals["travel_min"] += leg.minutes
        totals["visit_min"] += visit
        totals["wait_min"] += wait
        totals["co2_g"] += leg.co2_g + (candidate.carbon_kg or 0.0) * 1000
        totals["walking_km"] += leg.distance_km if leg.mode == "walk" else 0.0
        if leg.cost is None or entry_cost is None:
            totals["cost_unknown_items"] += (leg.cost is None) + (entry_cost is None)
        totals["cost"] += (leg.cost or 0.0) + (entry_cost or 0.0)
        totals["value"] += value
        stops.append({"candidate": candidate, "leg": leg, "arrive": arrive, "depart": depart, "visit_min": visit, "wait_min": wait, "free_min": free, "open_check": open_check, "weather_note": weather_note, "value": value})
        clock = depart
        position = (candidate.lat, candidate.lon)
    totals["end"] = clock
    return {"stops": stops, "totals": totals}


def _objective(simulation: dict[str, Any], request: PlanRequest) -> float:
    config = load_rules("itinerary")
    weights = (config["presets"].get(request.preset) or config["presets"]["balanced"])["weights"]
    totals = simulation["totals"]
    return weights["value"] * totals["value"] - weights["time"] * (totals["travel_min"] + totals["wait_min"]) - weights["cost"] * totals["cost"] - weights["carbon"] * totals["co2_g"] - weights["walking"] * totals["walking_km"]


def optimise(request: PlanRequest) -> dict[str, Any]:
    config = load_rules("itinerary")
    pool = [c for c in request.candidates if c.id not in set(request.excluded_ids) and c.lat is not None]
    locked = [c for lid in request.locked_ids for c in pool if c.id == lid]
    order: list[Candidate] = []
    unscheduled: list[dict[str, Any]] = []
    for candidate in locked:
        # Must-sees have no order of their own: put each where it fits best (a sunset spot's
        # not-before time still pushes it late).
        fits = []
        for index in range(len(order) + 1):
            trial = order[:index] + [candidate] + order[index:]
            simulation = _simulate(trial, request)
            if simulation is not None:
                fits.append((_objective(simulation, request), index))
        if not fits:
            unscheduled.append({"id": candidate.id, "name": candidate.name, "reason": "locked stop does not fit the time window or opening hours"})
        else:
            order.insert(max(fits)[1], candidate)
    remaining = [c for c in pool if c not in order]
    current = _simulate(order, request) or {"stops": [], "totals": {"value": 0}}
    current_score = _objective(current, request) if order else 0.0
    while remaining and len(order) < config["max_stops"]:
        best: tuple[float, int, Candidate, dict[str, Any]] | None = None
        for candidate in remaining:
            for index in range(len(order) + 1):
                trial = order[:index] + [candidate] + order[index:]
                simulation = _simulate(trial, request)
                if simulation is None:
                    continue
                score = _objective(simulation, request)
                if score > current_score and (best is None or score > best[0]):
                    best = (score, index, candidate, simulation)
        if best is None:
            break
        current_score, index, candidate, current = best
        order.insert(index, candidate)
        remaining.remove(candidate)

    reported = {item["id"] for item in unscheduled}
    for candidate in [c for c in remaining if c.id not in reported][:10]:
        unscheduled.append({"id": candidate.id, "name": candidate.name, "reason": "did not fit the time window, opening hours or preferences as well as the chosen stops"})

    simulation = _simulate(order, request) if order else {"stops": [], "totals": {"travel_min": 0, "visit_min": 0, "wait_min": 0, "cost": 0.0, "cost_unknown_items": 0, "co2_g": 0.0, "walking_km": 0.0, "value": 0.0, "end": request.start_time}}
    stops_out = []
    warnings: list[str] = []
    for position, stop in enumerate(simulation["stops"], start=1):
        candidate: Candidate = stop["candidate"]
        leg: _Leg = stop["leg"]
        if stop["open_check"] == "hours_unknown":
            warnings.append(f"Opening hours for {candidate.name} are not verified.")
        if stop["weather_note"]:
            warnings.append(f"{candidate.name}: {stop['weather_note']}.")
        stops_out.append({
            "position": position,
            "poi_id": candidate.id,
            "name": candidate.name,
            "category": candidate.category,
            "address": candidate.address,
            "lat": candidate.lat,
            "lon": candidate.lon,
            "arrive": stop["arrive"].strftime("%H:%M"),
            "depart": stop["depart"].strftime("%H:%M"),
            "visit_min": stop["visit_min"],
            "wait_min": stop["wait_min"],
            "free_min": stop.get("free_min", 0),
            "timing_note": request.timing_notes.get(candidate.id),
            "open_check": stop["open_check"],
            "entry_fee": candidate.entry_fee,
            "entry_cost": candidate.entry_cost,
            "carbon_kg": candidate.carbon_kg,
            "confidence": candidate.confidence_detail.get("label"),
            "conflicts": candidate.conflicts,
            "entry_fee_foreign": candidate.entry_fee_foreign,
            "fee_currency": candidate.fee_currency or request.currency,
            "fee_notes": candidate.fee_notes,
            "step_free": candidate.step_free,
            "walking_effort": candidate.walking_effort,
            "locked": candidate.id in request.locked_ids,
            "reasons": candidate.reasons[:3],
            "source": candidate.primary_source.source if candidate.primary_source else None,
            "leg": {"from": request.start_label if position == 1 else simulation["stops"][position - 2]["candidate"].name, "mode": leg.mode, "distance_km": leg.distance_km, "minutes": leg.minutes, "cost": leg.cost, "co2_g": leg.co2_g, "estimate": True},
        })
    totals = simulation["totals"]
    exact, exact_currency, exact_complete = sum_money([(s["candidate"].entry_cost, s["candidate"].fee_currency or request.currency) for s in simulation["stops"]] + [(s["leg"].cost, request.currency) for s in simulation["stops"]])
    used = int((totals["end"] - request.start_time).total_seconds() // 60) if stops_out else 0
    plan = {
        "id": uuid.uuid4().hex,
        "preset": request.preset,
        "start_label": request.start_label,
        "start": request.start_time.strftime("%Y-%m-%d %H:%M"),
        "end": totals["end"].strftime("%H:%M") if stops_out else None,
        "window_min": request.duration_min,
        "used_min": used,
        "stops": stops_out,
        "totals": {
            "stops": len(stops_out),
            "travel_min": totals["travel_min"],
            "visit_min": totals["visit_min"],
            "wait_min": totals["wait_min"],
            "cost": round(totals["cost"], 0),
            "cost_exact": str(exact),
            "cost_display": money_text(exact, exact_currency or request.currency),
            "cost_currency": exact_currency or request.currency,
            "cost_complete": totals["cost_unknown_items"] == 0 and exact_complete,
            "budget_cap": str(request.budget_cap) if request.budget_cap is not None else None,
            "within_budget": (exact <= request.budget_cap) if request.budget_cap is not None else None,
            "co2_g": round(totals["co2_g"], 0),
            "walking_km": round(totals["walking_km"], 2),
        },
        "warnings": list(dict.fromkeys(warnings)),
        "unscheduled": unscheduled,
        "weights": (config["presets"].get(request.preset) or config["presets"]["balanced"]),
        "estimates_note": "Travel times, fares and CO2 are estimates from straight-line distance; entry fees come from stored data.",
        "understood": request.understood,
        "travel_mode": request.travel_mode,
    }
    plan["explanation"] = explain_changes(request.previous, plan) if request.previous else [f"Built a {request.preset.replace('_', ' ')} plan with {len(stops_out)} stops in {used} of {request.duration_min} minutes."]
    return plan


def explain_changes(previous: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    before, after = previous.get("totals") or {}, plan["totals"]
    currency = after.get("cost_currency") or ""
    same = previous.get("preset") == plan["preset"]
    lines = ["Re-planned with the same priorities." if same else f"Re-planned for {plan['preset'].replace('_', ' ')}."]
    for key, label, unit in (("cost", "Estimated cost", f" {currency}".rstrip()), ("co2_g", "Estimated CO₂", " g"), ("walking_km", "Walking", " km"), ("travel_min", "Travel time", " min")):
        if key in before and before[key] != after[key]:
            direction = "down" if after[key] < before[key] else "up"
            lines.append(f"{label} {direction} from {before[key]:g}{unit} to {after[key]:g}{unit}.")
    before_ids = [s.get("poi_id") for s in previous.get("stops") or []]
    after_ids = [s["poi_id"] for s in plan["stops"]]
    added = [s["name"] for s in plan["stops"] if s["poi_id"] not in before_ids]
    removed = [s.get("name") for s in previous.get("stops") or [] if s.get("poi_id") not in after_ids]
    if added:
        lines.append("Added: " + ", ".join(added) + ".")
    if removed:
        lines.append("Dropped: " + ", ".join(filter(None, removed)) + ".")
    modes_before = {s.get("leg", {}).get("mode") for s in previous.get("stops") or []}
    modes_after = {s["leg"]["mode"] for s in plan["stops"]}
    if modes_before != modes_after:
        lines.append("Transport between stops: " + ", ".join(sorted(m.replace("_", " ") for m in modes_after)) + ".")
    return lines
