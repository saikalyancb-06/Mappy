"""Open-Meteo weather: live/dynamic data, cached briefly with explicit timestamps.

Weather is never stored in the knowledge base. On failure the snapshot says
``status: unavailable`` and callers omit weather-dependent claims.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import CACHE_TTL_WEATHER_S, OPEN_METEO_URL
from app.core.cache import cache_get, cache_set
from app.core.http import ProviderError, get_json
from app.core.rules import load_rules

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers", 81: "Showers",
    82: "Violent showers", 85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def _fetch(lat: float, lon: float) -> dict[str, Any]:
    key = f"{lat:.3f},{lon:.3f}"
    cached = cache_get("weather", key)
    if cached is not None:
        return {**cached["data"], "cached": True, "retrieved_at": cached["created_at"]}
    payload = get_json(
        "open_meteo",
        OPEN_METEO_URL,
        params={
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_probability_max,precipitation_sum,sunrise,sunset,uv_index_max",
            "timezone": "auto",
            "forecast_days": 7,
        },
        timeout=6.0,
    )
    entry = cache_set("weather", key, payload, CACHE_TTL_WEATHER_S, source="open-meteo")
    return {**payload, "cached": False, "retrieved_at": entry["created_at"]}


def _signals(apparent_max: float | None, rain_probability: float | None, code: int | None) -> list[str]:
    rules = load_rules("ranking")["weather"]
    signals = []
    if apparent_max is not None and apparent_max >= rules["heat_apparent_c"]:
        signals.append("heat")
    if (rain_probability is not None and rain_probability >= rules["rain_probability"]) or (code in RAIN_CODES):
        signals.append("rain")
    return signals


def get_weather(lat: float, lon: float, day_offset: int = 0) -> dict[str, Any]:
    """Weather snapshot for a point. ``day_offset`` selects the forecast day (0 = today)."""
    try:
        raw = _fetch(lat, lon)
    except ProviderError as exc:
        return {"status": "unavailable", "error": exc.as_dict(), "source": "open-meteo"}
    timezone_name = raw.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    daily = raw.get("daily") or {}
    dates = daily.get("time") or []
    index = min(max(day_offset, 0), len(dates) - 1) if dates else None

    def pick(name: str) -> Any:
        values = daily.get(name) or []
        return values[index] if index is not None and index < len(values) else None

    day = None
    if index is not None:
        code = pick("weather_code")
        day = {
            "date": dates[index],
            "summary": WMO_CODES.get(code, "Unknown conditions"),
            "weather_code": code,
            "temp_max_c": pick("temperature_2m_max"),
            "temp_min_c": pick("temperature_2m_min"),
            "apparent_max_c": pick("apparent_temperature_max"),
            "precipitation_probability_max": pick("precipitation_probability_max"),
            "precipitation_mm": pick("precipitation_sum"),
            "uv_index_max": pick("uv_index_max"),
            "sunrise": pick("sunrise"),
            "sunset": pick("sunset"),
        }
        day["signals"] = _signals(day["apparent_max_c"], day["precipitation_probability_max"], code)

    current = raw.get("current") or {}
    current_block = None
    if current and day_offset == 0:
        code = current.get("weather_code")
        current_block = {
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "apparent_c": current.get("apparent_temperature"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
            "weather_code": code,
            "summary": WMO_CODES.get(code, "Unknown conditions"),
            "is_day": bool(current.get("is_day")) if current.get("is_day") is not None else None,
        }

    hourly = raw.get("hourly") or {}
    hours = []
    for i, stamp in enumerate(hourly.get("time") or []):
        if not stamp.startswith(day["date"] if day else ""):
            continue
        hours.append({"time": stamp, "temperature_c": (hourly.get("temperature_2m") or [None])[i], "apparent_c": (hourly.get("apparent_temperature") or [None])[i], "precipitation_probability": (hourly.get("precipitation_probability") or [None])[i], "weather_code": (hourly.get("weather_code") or [None])[i]})

    daylight_left_min = None
    if day and day_offset == 0 and day.get("sunset"):
        try:
            sunset = datetime.fromisoformat(day["sunset"]).replace(tzinfo=tz)
            daylight_left_min = max(0, int((sunset - now_local).total_seconds() // 60))
        except ValueError:
            pass

    return {
        "status": "ok",
        "source": "open-meteo",
        "source_url": "https://open-meteo.com/",
        "retrieved_at": raw.get("retrieved_at"),
        "cached": raw.get("cached", False),
        "timezone": timezone_name,
        "local_time": now_local.isoformat(timespec="minutes"),
        "day_offset": day_offset,
        "current": current_block,
        "day": day,
        "hourly": hours,
        "daylight_left_min": daylight_left_min,
        "signals": sorted(set((day or {}).get("signals", []) + (["rain"] if current_block and current_block.get("weather_code") in RAIN_CODES else []))),
    }


def hour_conditions(weather: dict[str, Any], local_dt: datetime) -> dict[str, Any] | None:
    """Forecast for the hour containing ``local_dt`` (used by the itinerary planner)."""
    if weather.get("status") != "ok":
        return None
    stamp = local_dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    for hour in weather.get("hourly", []):
        if hour["time"] == stamp:
            return hour
    return None


def local_now(timezone_name: str | None) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name or "UTC"))
    except Exception:
        return datetime.now(ZoneInfo("UTC"))


def day_start(timezone_name: str | None, day_offset: int) -> datetime:
    now = local_now(timezone_name)
    return (now + timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
