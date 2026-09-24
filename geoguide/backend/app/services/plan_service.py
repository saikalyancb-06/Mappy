"""Plan building: candidates from discovery + saved places → optimiser → persistence."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import Trace
from app.core.rules import load_rules, taxonomy
from app.db.models import Itinerary, ItineraryItem, Trip
from app.db.session import SessionLocal
from app.geo.geo_context import GeoContext, destination_by_id
from app.geo.spatial import get_pois
from app.planning.itinerary import PlanRequest, optimise
from app.search.aggregator import aggregate
from app.services.discovery import DiscoveryRequest, discover
from app.weather.open_meteo import get_weather, local_now

_PART_START = {"morning": 7, "afternoon": 13, "evening": 16, "night": 18}


def _start_time(tz: str | None, day_offset: int, part_of_day: str | None, explicit: str | None) -> datetime:
    now = local_now(tz)
    if explicit and explicit != "now":
        try:
            hour, minute = (int(x) for x in explicit.split(":"))
            return (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            pass
    if day_offset > 0:
        return (now + timedelta(days=day_offset)).replace(hour=_PART_START.get(part_of_day or "", 8), minute=0, second=0, microsecond=0)
    if part_of_day and _PART_START.get(part_of_day, 0) > now.hour:
        return now.replace(hour=_PART_START[part_of_day], minute=0, second=0, microsecond=0)
    minute = (now.minute // 15 + 1) * 15
    return (now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute))


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
    trace: Trace | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    config = load_rules("itinerary")
    reference = geo.reference
    destination = destination_by_id(reference.destination_id)
    weather = get_weather(reference.lat, reference.lon, day_offset)
    tz = (destination.timezone if destination else None) or (weather.get("timezone") if weather.get("status") == "ok" else None)
    start_time = _start_time(tz, day_offset, part_of_day, start)
    if duration_min is None:
        duration_min = config["durations_min"].get(duration_key or "", None) or (240 if part_of_day in {"evening", "night"} else config["durations_min"]["4h"])
    # Evening plans end at the destination's sunset + 1h at most for open-air safety.
    if weather.get("status") == "ok" and weather.get("day") and weather["day"].get("sunset") and part_of_day in {"evening", "night"}:
        try:
            sunset = datetime.fromisoformat(weather["day"]["sunset"]).replace(tzinfo=start_time.tzinfo)
            duration_min = max(60, min(duration_min, int((sunset + timedelta(minutes=60) - start_time).total_seconds() // 60)))
        except ValueError:
            pass

    result = discover(DiscoveryRequest(
        reference=reference,
        profile_name="itinerary",
        kinds=set(taxonomy()["attraction_kinds"]),
        user=profile,
        local_time=start_time,
        time_sensitive=False,
        weather_signals=weather.get("signals") or [],
        limit=30,
        user_point=geo.user_point(),
        allow_web=False,
    ), trace=trace)
    locked_ids = list(locked_ids or [])
    extra = [c for c in get_pois(locked_ids, (reference.lat, reference.lon)) if c.id not in {x.id for x in result.candidates}]
    for candidate in extra:
        candidate.score = candidate.score or 0.6
    candidates, _ = aggregate([result.candidates, extra])
    start_point = (reference.lat, reference.lon)
    start_label = reference.label
    if geo.user_location and reference.origin != "user_location" and destination and geo.user_location.inside_destination_id == destination.id:
        start_point, start_label = (geo.user_location.lat, geo.user_location.lon), "your location"
    request = PlanRequest(
        candidates=candidates,
        start_point=start_point,
        start_label=start_label,
        start_time=start_time,
        duration_min=int(duration_min),
        preset=preset if preset in config["presets"] else "balanced",
        walking=profile.get("walking") or {"relaxed": "low", "packed": "high"}.get(profile.get("pace") or "", "moderate"),
        currency=destination.currency if destination else None,
        locked_ids=locked_ids,
        excluded_ids=list(excluded_ids or []),
        weather=weather,
        previous=previous,
    )
    plan = optimise(request)
    plan["destination_id"] = reference.destination_id
    plan["weather_signals"] = weather.get("signals") or []
    if trace:
        trace.step("itinerary", preset=plan["preset"], window_min=plan["window_min"], stops=[s["poi_id"] for s in plan["stops"]], totals=plan["totals"], unscheduled=len(plan["unscheduled"]))
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
