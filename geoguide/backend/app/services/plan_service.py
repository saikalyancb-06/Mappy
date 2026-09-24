"""Plan building and live re-optimisation.

A plan comes from: discovery candidates (filtered by the traveller's typed
wishes and profile) + saved/must-see places → constraint-aware optimiser →
persistence. Re-planning starts from the *current* state (time, position,
stops already done, a stop that ran long) and re-optimises only what is left.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.logging import Trace
from app.core.rules import load_rules, taxonomy
from app.core.text import normalize
from app.db.models import EntityAlias, Itinerary, ItineraryItem, Poi, Trip
from app.db.session import SessionLocal
from app.geo.geo_context import GeoContext, destination_by_id
from app.geo.spatial import get_pois
from app.planning.itinerary import PlanRequest, optimise
from app.query.constraints import Constraints, _negated_spans, parse_constraints
from app.search.aggregator import aggregate
from app.services.cost import to_decimal
from app.services.discovery import DiscoveryRequest, discover
from app.weather.open_meteo import get_weather, local_now

_PART_START = {"morning": 7, "afternoon": 13, "evening": 16, "night": 18}


@dataclass
class ReplanState:
    """Where the traveller actually is now, for re-optimising the rest of a plan."""

    previous: dict[str, Any]
    completed_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    current_stop_id: str | None = None  # still at this stop
    extra_minutes: int = 0  # staying this much longer than planned at the current stop / running late
    now: str | None = None  # "HH:MM" local; default: the destination's current time


def _clock(now: datetime, text: str | None, day_offset: int = 0) -> datetime | None:
    if not text:
        return None
    try:
        hour, minute = (int(x) for x in text.split(":"))
    except ValueError:
        return None
    return (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _start_time(tz: str | None, day_offset: int, part_of_day: str | None, explicit: str | None) -> datetime:
    now = local_now(tz)
    if explicit and explicit != "now":
        parsed = _clock(now, explicit, day_offset)
        if parsed:
            return parsed
    if day_offset > 0:
        return (now + timedelta(days=day_offset)).replace(hour=_PART_START.get(part_of_day or "", 8), minute=0, second=0, microsecond=0)
    if part_of_day and _PART_START.get(part_of_day, 0) > now.hour:
        return now.replace(hour=_PART_START[part_of_day], minute=0, second=0, microsecond=0)
    minute = (now.minute // 15 + 1) * 15
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)


def named_places(text: str, destination_id: str | None) -> tuple[list[str], list[str]]:
    """POIs the traveller names in their wishes: (must include, must exclude). Matched on names and aliases."""
    if not text or not destination_id:
        return [], []
    norm = f" {normalize(text)} "
    negated = " " + " ".join(normalize(span) for span in _negated_spans(text.lower(), load_rules("intents")["constraints"]["negations"])) + " "
    with SessionLocal() as db:
        pois = {p.id: p.name for p in db.scalars(select(Poi).where(Poi.destination_id == destination_id)).all()}
        aliases = db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "poi", EntityAlias.entity_id.in_(list(pois)))).all()
    names: dict[str, set[str]] = {pid: {normalize(name)} for pid, name in pois.items()}
    for alias in aliases:
        names.setdefault(alias.entity_id, set()).add(alias.normalized)
    include, exclude = [], []
    generic = {normalize(w) for entry in taxonomy()["categories"].values() for w in entry["synonyms"]}
    for pid, variants in names.items():
        for variant in sorted(variants, key=len, reverse=True):
            if len(variant) < 4 or variant in generic:
                continue
            if f" {variant} " in norm:
                (exclude if f" {variant} " in negated else include).append(pid)
                break
    return include, exclude


def build_plan(
    *,
    geo: GeoContext,
    profile: dict[str, Any],
    duration_min: int | None = None,
    duration_key: str | None = None,
    part_of_day: str | None = None,
    day_offset: int = 0,
    start: str | None = None,
    preset: str = "balanced",
    locked_ids: list[str] | None = None,
    excluded_ids: list[str] | None = None,
    previous: dict[str, Any] | None = None,
    user_id: str | None = None,
    wishes: str | None = None,
    constraints: Constraints | None = None,
    replan: ReplanState | None = None,
    trace: Trace | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    config = load_rules("itinerary")
    reference = geo.reference
    destination = destination_by_id(reference.destination_id)
    weather = get_weather(reference.lat, reference.lon, day_offset)
    tz = (destination.timezone if destination else None) or (weather.get("timezone") if weather.get("status") == "ok" else None)
    now = local_now(tz)
    constraints = constraints or (parse_constraints(wishes) if wishes else None)
    understood = list(constraints.understood) if constraints else []
    locked_ids = list(dict.fromkeys(locked_ids or []))
    excluded_ids = list(dict.fromkeys(excluded_ids or []))
    if wishes:
        must, never = named_places(wishes, reference.destination_id)
        if must:
            locked_ids = list(dict.fromkeys([*must, *locked_ids]))
            understood.append("Must include: " + ", ".join(c.name for c in get_pois(must)))
        if never:
            excluded_ids = list(dict.fromkeys([*excluded_ids, *never]))
            understood.append("Leaving out: " + ", ".join(c.name for c in get_pois(never)))

    # ---- time window
    start_time = _start_time(tz, day_offset, part_of_day, start)
    end_time = None
    if constraints and constraints.window_start and not start:
        start_time = _clock(now, constraints.window_start, day_offset) or start_time
        if day_offset == 0 and start_time < now:
            start_time = now.replace(second=0, microsecond=0)
    if constraints and constraints.window_end:
        end_time = _clock(now, constraints.window_end, day_offset)
    if duration_min is None and constraints and constraints.available_minutes and not end_time:
        duration_min = constraints.available_minutes

    # ---- re-optimise from the current state
    start_point, start_label = (reference.lat, reference.lon), reference.label
    if geo.user_location and reference.origin != "user_location" and destination and geo.user_location.inside_destination_id == destination.id:
        start_point, start_label = (geo.user_location.lat, geo.user_location.lon), "your location"
    if replan is not None:
        prev = replan.previous
        done = set(replan.completed_ids) | set(replan.skipped_ids)
        stops = prev.get("stops") or []
        current = next((s for s in stops if s["poi_id"] == replan.current_stop_id), None)
        # Clock times in the previous plan belong to that plan's day, which may not be today.
        prev_start = datetime.strptime(prev["start"], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        clock = _clock(prev_start, replan.now) or max(now.replace(second=0, microsecond=0), prev_start)
        if current:
            planned_depart = _clock(prev_start, current["depart"]) or clock
            start_time = max(clock, planned_depart) + timedelta(minutes=replan.extra_minutes)
            start_point, start_label = (current["lat"], current["lon"]), current["name"]
            done.add(current["poi_id"])
        else:
            start_time = clock + timedelta(minutes=replan.extra_minutes)
            last_done = [s for s in stops if s["poi_id"] in done]
            if geo.user_location:
                start_point, start_label = (geo.user_location.lat, geo.user_location.lon), "your location"
            elif last_done:
                start_point, start_label = (last_done[-1]["lat"], last_done[-1]["lon"]), last_done[-1]["name"]
        end_time = prev_start + timedelta(minutes=int(prev["window_min"]))
        excluded_ids = list(dict.fromkeys([*excluded_ids, *done]))
        locked_ids = [i for i in locked_ids if i not in done]
        previous = {**prev, "stops": [s for s in stops if s["poi_id"] not in done]}
        understood.append(f"Re-planning from {start_label} at {start_time.strftime('%H:%M')} until {end_time.strftime('%H:%M')}" + (f" (+{replan.extra_minutes} min)" if replan.extra_minutes else ""))
        preset = prev.get("preset", preset)

    if end_time is not None:
        duration_min = max(0, int((end_time - start_time).total_seconds() // 60))
    if duration_min is None:
        duration_min = config["durations_min"].get(duration_key or "", None) or (240 if part_of_day in {"evening", "night"} else config["durations_min"]["4h"])
    if weather.get("status") == "ok" and (weather.get("day") or {}).get("sunset") and part_of_day in {"evening", "night"} and end_time is None:
        try:
            sunset = datetime.fromisoformat(weather["day"]["sunset"]).replace(tzinfo=start_time.tzinfo)
            duration_min = max(60, min(duration_min, int((sunset + timedelta(minutes=60) - start_time).total_seconds() // 60)))
        except ValueError:
            pass

    # ---- budget: an explicit amount in the wishes, else the traveller's daily budget
    currency = destination.currency if destination else None
    budget_cap: Decimal | None = None
    if constraints and constraints.max_cost and (not currency or constraints.cost_currency in (None, currency)):
        budget_cap = to_decimal(constraints.max_cost)
    elif profile.get("max_daily_budget") and (not profile.get("budget_currency") or profile.get("budget_currency") == currency):
        budget_cap = to_decimal(profile.get("max_daily_budget"))
        if budget_cap is not None:
            understood.append(f"Keeping within your daily budget ({currency or ''} {budget_cap})".replace("  ", " "))

    request_constraints = constraints
    if constraints is not None and constraints.max_cost:
        # The amount is a plan-wide cap handled by the optimiser, not a per-place filter.
        request_constraints = Constraints(**{**constraints.as_dict(), "max_cost": None, "limit": None})
    elif constraints is not None:
        request_constraints = Constraints(**{**constraints.as_dict(), "limit": None})
    result = discover(DiscoveryRequest(
        reference=reference,
        profile_name="itinerary",
        kinds=set(taxonomy()["attraction_kinds"]),
        user=profile,
        local_time=start_time,
        time_sensitive=False,
        weather_signals=weather.get("signals") or [],
        limit=40,
        user_point=geo.user_point(),
        allow_web=False,
        constraints=request_constraints,
        exclude_ids=set(excluded_ids),
    ), trace=trace)
    extra = [c for c in get_pois(locked_ids, (reference.lat, reference.lon)) if c.id not in {x.id for x in result.candidates}]
    for candidate in extra:
        candidate.score = candidate.score or 0.6
    candidates, _ = aggregate([result.candidates, extra])
    if previous and replan is not None:
        # Places that were already in the plan keep a small preference so the plan stays stable.
        kept = {s["poi_id"] for s in previous.get("stops") or []}
        for candidate in candidates:
            if candidate.id in kept:
                candidate.score = (candidate.score or 0.3) + 0.1
    request = PlanRequest(
        candidates=candidates,
        start_point=start_point,
        start_label=start_label,
        start_time=start_time,
        duration_min=int(duration_min),
        preset=preset if preset in config["presets"] else "balanced",
        walking=profile.get("walking") or {"relaxed": "low", "packed": "high"}.get(profile.get("pace") or "", "moderate"),
        currency=currency,
        locked_ids=locked_ids,
        excluded_ids=excluded_ids,
        weather=weather,
        previous=previous,
        budget_cap=budget_cap,
        travel_mode=(constraints.travel_mode if constraints else None) or profile.get("travel_mode"),
        understood=understood,
    )
    plan = optimise(request)
    plan["destination_id"] = reference.destination_id
    plan["weather_signals"] = weather.get("signals") or []
    plan["wishes"] = wishes
    plan["replanned"] = replan is not None
    if replan is not None:
        plan["completed"] = [s for s in replan.previous.get("stops") or [] if s["poi_id"] in (set(replan.completed_ids) | ({replan.current_stop_id} if replan.current_stop_id else set()))]
    missing = [i for i in locked_ids if i not in {s["poi_id"] for s in plan["stops"]}]
    if missing:
        plan["warnings"] = [*plan["warnings"], *[f"Couldn't fit {c.name} in this window (time, opening hours or budget)." for c in get_pois(missing)]]
    if trace:
        trace.step("itinerary", preset=plan["preset"], window_min=plan["window_min"], stops=[s["poi_id"] for s in plan["stops"]], totals=plan["totals"], unscheduled=len(plan["unscheduled"]), understood=understood)
    _persist(plan, user_id, reference.destination_id, start_time)
    plan["_candidates"] = candidates
    return plan, weather, list(result.provider_errors)


def _persist(plan: dict[str, Any], user_id: str | None, destination_id: str | None, start_time: datetime) -> None:
    try:
        with SessionLocal() as db:
            trip_id = uuid.uuid4().hex
            db.add(Trip(id=trip_id, user_id=user_id, destination_id=destination_id, start_time=start_time.isoformat(), duration_hours=round(plan["window_min"] / 60, 2)))
            db.add(Itinerary(id=plan["id"], trip_id=trip_id, preset=plan["preset"], summary=" → ".join(s["name"] for s in plan["stops"]), plan=json.dumps(plan, default=str)))
            for stop in plan["stops"]:
                db.add(ItineraryItem(id=uuid.uuid4().hex, itinerary_id=plan["id"], poi_id=stop["poi_id"], position=stop["position"], start_time=stop["arrive"], end_time=stop["depart"], locked=stop["locked"]))
            db.commit()
    except Exception:
        pass  # persistence is best-effort; the plan is still returned

