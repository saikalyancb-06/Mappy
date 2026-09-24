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
from app.query.constraints import wish_label
from app.services.discovery import DiscoveryRequest, discover, interleave_by_wish, matches_wish
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
    deck: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Build a plan. With ``deck=True``, return the candidate cards for swiping instead of a plan."""
    config = load_rules("itinerary")
    reference = geo.reference
    destination = destination_by_id(reference.destination_id)
    if destination and reference.origin == "user_location" and reference.radius_km < (destination.coverage_radius_km or 0):
        # A day plan covers the whole destination the traveller is in; it still starts from where they are.
        from dataclasses import replace

        reference = replace(reference, radius_km=float(destination.coverage_radius_km))
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
    candidates.sort(key=lambda c: -(c.score or 0))
    items = (constraints.wishes if constraints else None) or []
    place_name = destination.name if destination else (f"within {reference.radius_km:g} km of you" if reference.origin == "user_location" else reference.label)
    if deck:
        cards = interleave_by_wish(candidates, items) if len(items) > 1 else candidates
        excluded = set(excluded_ids)
        cards = [c for c in cards if c.id not in excluded]
        for card in cards:
            card.scores["wish"] = next((wish_label(item) for item in items if matches_wish(card, item)), None)
        deck_result = {
            "cards": [c.as_dict() for c in cards[:DECK_SIZE]],
            "understood": understood,
            "wish_coverage": _wish_coverage(candidates, items, budget_cap, currency, place_name, excluded),
            "budget_cap": str(budget_cap) if budget_cap is not None else None,
            "currency": currency,
        }
        return deck_result, weather, list(result.provider_errors)
    user_locks = list(locked_ids)
    all_candidates = list(candidates)
    coverage: list[dict[str, Any]] = []
    if items:
        candidates, locked_ids, coverage = _fill_wishes(candidates, items, locked_ids, set(excluded_ids), budget_cap, currency, place_name)
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
    plan = _optimise_covering(request, coverage, items, all_candidates, weather, start_time, int(duration_min))
    plan["destination_id"] = reference.destination_id
    plan["timezone"] = tz  # IANA zone of the plan's start time (for calendar export)
    plan["weather_signals"] = weather.get("signals") or []
    plan["wishes"] = wishes
    plan["replanned"] = replan is not None
    if replan is not None:
        plan["completed"] = [s for s in replan.previous.get("stops") or [] if s["poi_id"] in (set(replan.completed_ids) | ({replan.current_stop_id} if replan.current_stop_id else set()))]
    planned = {s["poi_id"] for s in plan["stops"]}
    plan["wish_coverage"] = coverage
    plan["understood"] = [*plan.get("understood", []), *[entry["timing_note"] for entry in coverage if entry.get("timing_note")]]
    plan["warnings"] = [*plan["warnings"], *[entry["note"] for entry in coverage if entry["status"] != "planned" and entry.get("note")]]
    missing = [i for i in user_locks if i not in planned]
    if missing:
        plan["warnings"] = [*plan["warnings"], *[f"Couldn't fit {c.name} in this window (time, opening hours or budget)." for c in get_pois(missing)]]
    if trace:
        trace.step("itinerary", preset=plan["preset"], window_min=plan["window_min"], stops=[s["poi_id"] for s in plan["stops"]], totals=plan["totals"], unscheduled=len(plan["unscheduled"]), understood=understood)
    _persist(plan, user_id, reference.destination_id, start_time)
    plan["_candidates"] = candidates
    return plan, weather, list(result.provider_errors)


DECK_SIZE = 24


def _money(amount: Decimal | None, currency: str | None) -> str:
    from app.services.cost import money_text

    return money_text(amount, currency) or "?"


def _wish_coverage(candidates: list[Any], items: list[dict[str, Any]], budget_cap: Decimal | None, currency: str | None, place: str, excluded: set[str]) -> list[dict[str, Any]]:
    """For each thing asked for: is there anything stored for it, and anything within budget?"""
    out = []
    for item in items:
        label = wish_label(item)
        matches = [c for c in candidates if matches_wish(c, item) and c.id not in excluded]
        entry = {"key": item["key"], "label": item["label"], "wish": label, "quantity": item.get("quantity"), "status": "available", "note": None, "picks": [], "pick_names": [], "matches": len(matches)}
        if not matches:
            entry["status"], entry["note"] = "none_available", f"No verified {_plural_label(item)} found {place if place.startswith('within') else 'in ' + place}, so the plan can't include one."
        elif budget_cap is not None:
            affordable = [c for c in matches if (to_decimal(c.entry_cost) or Decimal(0)) <= budget_cap]
            if not affordable:
                cheapest = min(matches, key=lambda c: to_decimal(c.entry_cost) or Decimal(0))
                entry["status"] = "over_budget"
                entry["note"] = (f"The only {item['label']} ({cheapest.name}) costs {_money(to_decimal(cheapest.entry_cost), cheapest.fee_currency or currency)}" if len(matches) == 1 else f"The cheapest {item['label']} ({cheapest.name}) costs {_money(to_decimal(cheapest.entry_cost), cheapest.fee_currency or currency)}") + f", over your {_money(budget_cap, currency)} budget."
        out.append(entry)
    return out


def _plural_label(item: dict[str, Any]) -> str:
    from app.query.constraints import _plural

    return _plural(item["label"].lower())


def _mark_coverage(plan: dict[str, Any], coverage: list[dict[str, Any]], items: list[dict[str, Any]], by_id: dict[str, Any]) -> int:
    """Set each wish's status from the plan; returns how many wishes the plan covers."""
    planned = {s["poi_id"] for s in plan["stops"]}
    covered = 0
    for item, entry in zip(items, coverage):
        if entry["status"] in {"none_available", "over_budget"}:
            continue
        if entry.get("open_ended"):
            hits = [s["name"] for s in plan["stops"] if s["poi_id"] in by_id and matches_wish(by_id[s["poi_id"]], item)]
            entry["status"], entry["pick_names"] = ("planned", hits) if hits else ("didnt_fit", [])
        else:
            hits = [pid for pid in entry["picks"] if pid in planned]
            entry["status"] = "planned" if hits else "didnt_fit"
        covered += entry["status"] == "planned"
    return covered


def _optimise_covering(request: PlanRequest, coverage: list[dict[str, Any]], items: list[dict[str, Any]], candidates: list[Any], weather: dict[str, Any], start_time: datetime, duration_min: int) -> dict[str, Any]:
    """Optimise, then swap in alternatives for one-of wishes that didn't fit (up to 3 rounds), and
    explain what still doesn't fit: the budget (entry + transport) or the time window/opening hours."""
    by_id = {c.id: c for c in candidates}
    capped = [item for item in items if item.get("quantity")]
    open_ended = [item for item in items if not item.get("quantity")]
    # Places that only match one-of wishes enter the pool only as that wish's current pick.
    base_pool = [c for c in candidates if not any(matches_wish(c, i) for i in capped) or any(matches_wish(c, i) for i in open_ended) or c.id in request.locked_ids and not any(c.id in e.get("picks", []) for e in coverage)]
    base_locks = [pid for pid in request.locked_ids if not any(pid in entry.get("picks", []) for entry in coverage)]

    def setup() -> None:
        early = [pid for e in coverage if e.get("timing") == "early" for pid in e.get("picks", [])]
        late = [pid for e in coverage if e.get("timing") == "late" for pid in e.get("picks", [])]
        middle = [pid for e in coverage if e.get("timing") not in {"early", "late"} for pid in e.get("picks", [])]
        request.locked_ids = list(dict.fromkeys([*early, *base_locks, *middle, *late]))
        picks = {pid for e in coverage for pid in e.get("picks", [])}
        request.candidates = [*base_pool, *[by_id[pid] for pid in picks if pid in by_id and by_id[pid] not in base_pool]]
        request.not_before, request.timing_notes = _sunset_timing(coverage, weather, start_time, duration_min)

    def attempt() -> dict[str, Any]:
        setup()
        timed = optimise(request)
        placed = {s["poi_id"] for s in timed["stops"]}
        if not request.not_before or all(pid in placed for pid in request.not_before):
            return timed
        # A sunset spot that closes before sunset: try visiting it earlier, keep whichever plan covers more.
        timed_covered = _mark_coverage(timed, [dict(e) for e in coverage], items, by_id)
        for pid in [pid for pid in request.not_before if pid not in placed]:
            request.not_before.pop(pid)
            request.timing_notes[pid] = "couldn't be timed for sunset alongside the rest of the plan, so visited earlier"
        untimed = optimise(request)
        untimed_covered = _mark_coverage(untimed, [dict(e) for e in coverage], items, by_id)
        return untimed if (untimed_covered, len(untimed["stops"])) > (timed_covered, len(timed["stops"])) else timed

    plan = attempt()
    best, best_covered = plan, _mark_coverage(plan, coverage, items, by_id)
    saved = [dict(e) for e in coverage]
    for _ in range(3):
        stuck = [e for e in coverage if e["status"] == "didnt_fit" and e.get("alternatives")]
        if not stuck:
            break
        for entry in stuck:
            entry["picks"] = [entry["alternatives"].pop(0)]
            entry["pick_names"] = [by_id[entry["picks"][0]].name] if entry["picks"][0] in by_id else []
        plan = attempt()
        covered = _mark_coverage(plan, coverage, items, by_id)
        if covered > best_covered:
            best, best_covered, saved = plan, covered, [dict(e) for e in coverage]
    for entry, kept in zip(coverage, saved):
        entry.clear()
        entry.update(kept)
    setup()  # the request as it was for the kept plan, for explaining what didn't fit
    for entry in coverage:
        if entry["status"] != "didnt_fit":
            continue
        names = ", ".join(entry.get("pick_names") or []) or "any of them"
        reason = "in this time window and opening hours"
        if request.budget_cap is not None and entry.get("picks"):
            relaxed = PlanRequest(**{**request.__dict__, "budget_cap": None, "previous": None})
            if any(s["poi_id"] in entry["picks"] for s in optimise(relaxed)["stops"]):
                reason = f"within the {_money(request.budget_cap, request.currency)} budget (entry fees plus estimated transport)"
        entry["note"] = f"Couldn't fit a {entry['label']} ({names}) {reason}." if not entry.get("open_ended") else f"Couldn't fit any {_plural_label({'label': entry['label']})} {reason}."
    return best


def _sunset_timing(coverage: list[dict[str, Any]], weather: dict[str, Any], start_time: datetime, duration_min: int) -> tuple[dict[str, datetime], dict[str, str]]:
    """Reach "late" wishes (sunset spots) shortly before the forecast sunset, when it falls in the window."""
    sunset_text = ((weather or {}).get("day") or {}).get("sunset") if (weather or {}).get("status") == "ok" else None
    if not sunset_text:
        return {}, {}
    try:
        sunset = datetime.fromisoformat(sunset_text).replace(tzinfo=start_time.tzinfo)
    except ValueError:
        return {}, {}
    arrive = sunset - timedelta(minutes=load_rules("itinerary").get("sunset_lead_min", 45))
    end = start_time + timedelta(minutes=duration_min)
    late = [entry for entry in coverage if entry.get("timing") == "late" and entry.get("picks")]
    if not late or arrive < start_time:
        return {}, {}
    if arrive + timedelta(minutes=30) > end:
        for entry in late:
            entry["timing_note"] = f"Sunset is at {sunset.strftime('%H:%M')}, after this plan ends at {end.strftime('%H:%M')}; the sunset spot is placed last. Extend the day to catch it."
        return {}, {}
    not_before, notes = {}, {}
    for entry in coverage:
        if entry.get("timing") == "late":
            for pid in entry["picks"]:
                not_before[pid] = arrive
                notes[pid] = f"timed for sunset ({sunset.strftime('%H:%M')})"
    return not_before, notes


def _fill_wishes(candidates: list[Any], items: list[dict[str, Any]], locked_ids: list[str], excluded: set[str], budget_cap: Decimal | None, currency: str | None, place: str) -> tuple[list[Any], list[str], list[dict[str, Any]]]:
    """Cover every wish: lock its best affordable match(es), cap singular wishes ("a park" → one park),
    and schedule sunset spots last and sunrise spots first. Returns (pool, locks in order, coverage)."""
    coverage = _wish_coverage(candidates, items, budget_cap, currency, place, excluded)
    taken: set[str] = set(locked_ids)
    spent = Decimal(0)  # entry fees of the picks so far; together they must stay within the budget
    early, middle, late = [], [], []

    def cheapest(item: dict[str, Any]) -> Decimal:
        costs = [to_decimal(c.entry_cost) or Decimal(0) for c in candidates if matches_wish(c, item) and c.id not in excluded]
        return min(costs) if costs else Decimal(0)

    for index, (item, entry) in enumerate(zip(items, coverage)):
        if entry["status"] != "available":
            continue
        # Leave room for the cheapest option of each later one-of wish.
        reserve = sum((cheapest(later) for later, later_entry in zip(items[index + 1:], coverage[index + 1:]) if later.get("quantity") and later_entry["status"] == "available"), Decimal(0))
        affordable = [c for c in candidates if matches_wish(c, item) and c.id not in excluded and (budget_cap is None or (to_decimal(c.entry_cost) or Decimal(0)) <= budget_cap)]
        affordable.sort(key=lambda c: (c.category not in item["categories"], -(c.score or 0)))  # the named kind of place first
        already = [c for c in affordable if c.id in locked_ids]
        if not item.get("quantity"):
            # Open-ended ("temples"): the optimiser picks as many nearby ones as fit; nothing is locked.
            entry.update({"status": "picked", "picks": [], "pick_names": [], "open_ended": True, "timing": item.get("timing")})
            continue
        wanted = max(0, item["quantity"] - len(already))
        picks = []
        for candidate in affordable:
            if len(picks) >= wanted:
                break
            cost = to_decimal(candidate.entry_cost) or Decimal(0)
            if candidate.id in taken or (budget_cap is not None and spent + cost + reserve > budget_cap):
                continue
            picks.append(candidate)
            spent += cost
            taken.add(candidate.id)
        if not picks and not already and budget_cap is not None:
            entry["status"], entry["note"] = "over_budget", f"Your other picks use the budget, so no {item['label']} fits within {_money(budget_cap, currency)}."
            continue
        chosen = already[: item.get("quantity") or 1] + picks
        entry.update({"status": "picked", "picks": [c.id for c in chosen], "pick_names": [c.name for c in chosen], "timing": item.get("timing"), "alternatives": [c.id for c in affordable if c.id not in taken][:6]})
        bucket = late if item.get("timing") == "late" else early if item.get("timing") == "early" else middle
        bucket.extend(c.id for c in picks)
    capped = [item for item in items if item.get("quantity")]
    open_ended = [item for item in items if not item.get("quantity")]
    kept_ids = taken
    pool = [
        c for c in candidates
        if c.id in kept_ids or any(matches_wish(c, item) for item in open_ended) or not any(matches_wish(c, item) for item in capped)
    ]
    locks = list(dict.fromkeys([*early, *locked_ids, *middle, *late]))
    return pool, locks, coverage


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

