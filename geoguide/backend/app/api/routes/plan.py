from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.api.common import geo_for
from app.core.logging import Trace
from app.core.rules import load_rules
from app.services.plan_service import build_plan
from app.services.profile import resolve_profile, user_from_authorization

router = APIRouter(prefix="/api")


@router.post("/plan")
def plan(payload: dict | None, authorization: str | None = Header(default=None)) -> dict:
    safe = payload or {}
    config = load_rules("itinerary")
    duration = safe.get("duration") or "4h"
    duration_min = None
    duration_key = None
    if isinstance(duration, (int, float)):
        duration_min = int(duration)
    elif duration in config["durations_min"]:
        duration_key = duration
    else:
        raise HTTPException(status_code=422, detail="duration must be 2h, 4h, full or a number of minutes.")
    if duration_min is not None and not 30 <= duration_min <= 720:
        raise HTTPException(status_code=422, detail="duration must be between 30 and 720 minutes.")
    preset = str(safe.get("preset") or "balanced")
    if preset not in config["presets"]:
        raise HTTPException(status_code=422, detail=f"preset must be one of {sorted(config['presets'])}.")
    origin = str(safe.get("origin") or "auto")
    geo = geo_for(origin if origin in {"auto", "user", "destination"} else "auto", safe.get("user_location") if isinstance(safe.get("user_location"), dict) else None, safe.get("active_destination"))
    profile = resolve_profile(authorization, safe.get("profile") if isinstance(safe.get("profile"), dict) else None)
    user = user_from_authorization(authorization)
    trace = Trace("plan")
    day_offset = int(safe.get("day_offset") or 0)
    plan_data, weather, errors = build_plan(
        geo=geo,
        profile=profile,
        duration_min=duration_min,
        duration_key=duration_key,
        day_offset=max(0, min(day_offset, 6)),
        start=str(safe.get("start")) if safe.get("start") else None,
        preset=preset,
        locked_ids=[str(i) for i in safe.get("locked_ids") or []],
        excluded_ids=[str(i) for i in safe.get("excluded_ids") or []],
        previous=safe.get("previous") if isinstance(safe.get("previous"), dict) else None,
        user_id=user.id if user else None,
        trace=trace,
    )
    trace.emit()
    plan_data.pop("_candidates", None)
    return {"plan": plan_data, "weather": weather, "geo_context": geo.as_dict(), "provider_errors": errors, "request_id": trace.request_id}
