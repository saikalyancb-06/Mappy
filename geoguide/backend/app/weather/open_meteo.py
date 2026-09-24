"""Open-Meteo weather: live/dynamic data, cached briefly with explicit timestamps.

Weather is never stored in the knowledge base. On failure the snapshot says
``status: unavailable`` and callers omit weather-dependent claims.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import CACHE_TTL_WEATHER_S, OPEN_METEO_ARCHIVE_URL, OPEN_METEO_URL
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
FORECAST_DAYS = 16  # the provider's forecast horizon
RECENT_PAST_DAYS = 90  # the forecast API also serves recent past days
TYPICAL_YEARS = 3  # years averaged for "typical for this date"
ARCHIVE_TTL_S = 30 * 86400
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
            "forecast_days": FORECAST_DAYS,
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
        fallback = dataset_weather(lat, lon, day_offset)
        if fallback:
            fallback["live_error"] = exc.as_dict()
            return fallback
        return {"status": "unavailable", "error": exc.as_dict(), "source": "open-meteo"}
    timezone_name = raw.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    daily = raw.get("daily") or {}
    dates = daily.get("time") or []
    index = day_offset if 0 <= day_offset < len(dates) else None
    if index is None and dates:
        return {"status": "unavailable", "error": {"source": "open_meteo", "code": "out_of_range", "message": f"No forecast {day_offset} days ahead."}, "source": "open-meteo"}

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
        "live": True,
        "basis": "forecast",
        "basis_label": "Live forecast (Open-Meteo)",
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


_DATASET_CONDITIONS = {"clear": ("Clear sky", 0), "partly_cloudy": ("Partly cloudy", 2), "cloudy": ("Overcast", 3), "haze": ("Haze", 45), "light_rain": ("Light rain", 61), "heavy_rain": ("Heavy rain", 65)}


def dataset_weather(lat: float, lon: float, day_offset: int = 0) -> dict[str, Any] | None:
    from app.geo.geocoding import nearest_destination

    destination = nearest_destination(lat, lon)
    if destination is None:
        return None
    target = (local_now(destination.timezone) + timedelta(days=day_offset)).date()
    result = dataset_weather_on(destination.id, target, destination.timezone)
    if result:
        result["day_offset"] = day_offset
    return result


def dataset_weather_on(destination_id: str | None, target: date, timezone_name: str | None) -> dict[str, Any] | None:
    """Daily weather from an imported dataset for the destination containing the point.

    Only used when live weather is unavailable, and always labelled ``live: False``;
    there is no "current conditions" block because a daily record cannot say what it is like now.
    """
    from app.db.models import WeatherDaily
    from app.db.session import SessionLocal

    if not destination_id:
        return None
    now_local = local_now(timezone_name)
    with SessionLocal() as db:
        row = db.query(WeatherDaily).filter(WeatherDaily.destination_id == destination_id, WeatherDaily.for_date == target.isoformat()).first()
    if row is None:
        return None
    summary, code = _DATASET_CONDITIONS.get(row.condition or "", (str(row.condition or "Unknown").replace("_", " ").title(), None))
    rules = load_rules("ranking")["weather"]
    signals = []
    if row.feels_like_c is not None and row.feels_like_c >= rules["heat_apparent_c"]:
        signals.append("heat")
    if (row.precipitation_mm or 0) >= 5 or (code in RAIN_CODES):
        signals.append("rain")
    day = {
        "date": row.for_date, "summary": summary, "weather_code": code, "temp_max_c": row.temp_max_c, "temp_min_c": row.temp_min_c,
        "apparent_max_c": row.feels_like_c, "precipitation_probability_max": None, "precipitation_mm": row.precipitation_mm,
        "humidity_pct": row.humidity_pct, "wind_kph": row.wind_kph, "uv_index_max": None, "sunrise": None, "sunset": None,
        "is_extreme": bool(row.is_extreme), "signals": signals,
    }
    return {
        "status": "ok", "live": False, "source": "dataset weather_daily", "source_url": None,
        "basis": "dataset", "basis_label": "Dataset daily record (not a live forecast)",
        "note": "Live weather is unavailable; showing the dataset's daily record for this date.",
        "retrieved_at": row.updated_at.isoformat() + "Z" if row.updated_at else None, "cached": False,
        "timezone": timezone_name, "local_time": now_local.isoformat(timespec="minutes"), "day_offset": (target - now_local.date()).days,
        "current": None, "day": day, "hourly": [], "daylight_left_min": None, "signals": signals,
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


# ---- weather for a selected date -----------------------------------------------------------

_DAILY_FIELDS = "weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_sum,sunrise,sunset"


def _fetch_daily(url: str, lat: float, lon: float, start: date, end: date, ttl_s: int) -> dict[str, Any]:
    key = f"{url}|{lat:.3f},{lon:.3f}|{start}|{end}"
    cached = cache_get("weather_daily", key)
    if cached is not None:
        return {**cached["data"], "cached": True, "retrieved_at": cached["created_at"]}
    payload = get_json("open_meteo", url, params={"latitude": round(lat, 4), "longitude": round(lon, 4), "daily": _DAILY_FIELDS, "timezone": "auto", "start_date": start.isoformat(), "end_date": end.isoformat()}, timeout=8.0)
    entry = cache_set("weather_daily", key, payload, ttl_s, source="open-meteo")
    return {**payload, "cached": False, "retrieved_at": entry["created_at"]}


def _day_block(daily: dict[str, Any], index: int) -> dict[str, Any]:
    def pick(name: str) -> Any:
        values = daily.get(name) or []
        return values[index] if index < len(values) else None

    code = pick("weather_code")
    day = {
        "date": (daily.get("time") or [None])[index], "summary": WMO_CODES.get(code, "Unknown conditions"), "weather_code": code,
        "temp_max_c": pick("temperature_2m_max"), "temp_min_c": pick("temperature_2m_min"), "apparent_max_c": pick("apparent_temperature_max"),
        "precipitation_probability_max": pick("precipitation_probability_max"), "precipitation_mm": pick("precipitation_sum"),
        "uv_index_max": pick("uv_index_max"), "sunrise": pick("sunrise"), "sunset": pick("sunset"),
    }
    day["signals"] = _signals(day["apparent_max_c"], day["precipitation_probability_max"], code) + (["rain"] if (day["precipitation_mm"] or 0) >= 5 and code not in RAIN_CODES else [])
    return day


def _snapshot(day: dict[str, Any], *, basis: str, label: str, live: bool, timezone_name: str | None, retrieved_at: str | None, cached: bool, source_url: str, note: str | None = None, offset: int = 0) -> dict[str, Any]:
    return {
        "status": "ok", "live": live, "basis": basis, "basis_label": label, "source": "open-meteo", "source_url": source_url,
        "retrieved_at": retrieved_at, "cached": cached, "timezone": timezone_name, "local_time": local_now(timezone_name).isoformat(timespec="minutes"),
        "day_offset": offset, "current": None, "day": day, "hourly": [], "daylight_left_min": None, "signals": sorted(set(day.get("signals") or [])), "note": note,
    }


def _typical(lat: float, lon: float, target: date, timezone_name: str | None, offset: int) -> dict[str, Any] | None:
    """Average of the same calendar date in recent years, labelled as typical, never as a forecast."""
    days, years = [], []
    for back in range(1, TYPICAL_YEARS + 1):
        try:
            day = target.replace(year=target.year - back)
        except ValueError:  # 29 Feb
            day = target.replace(year=target.year - back, day=28)
        if day >= local_now(timezone_name).date() - timedelta(days=5):
            continue  # the archive lags a few days; only use settled records
        try:
            raw = _fetch_daily(OPEN_METEO_ARCHIVE_URL, lat, lon, day, day, ARCHIVE_TTL_S)
        except ProviderError:
            continue
        daily = raw.get("daily") or {}
        if daily.get("time"):
            days.append(_day_block(daily, 0))
            years.append(day.year)
    if not days:
        return None

    def mean(name: str) -> float | None:
        values = [d[name] for d in days if d.get(name) is not None]
        return round(sum(values) / len(values), 1) if values else None

    rainy = sum(1 for d in days if "rain" in d["signals"])
    day = {
        "date": target.isoformat(), "summary": f"Rain on {rainy} of the last {len(days)} years on this date" if rainy else f"Dry on this date in each of the last {len(days)} years",
        "weather_code": None, "temp_max_c": mean("temp_max_c"), "temp_min_c": mean("temp_min_c"), "apparent_max_c": mean("apparent_max_c"),
        "precipitation_probability_max": None, "precipitation_mm": mean("precipitation_mm"), "uv_index_max": None, "sunrise": None, "sunset": None,
        "rain_years": rainy, "years": sorted(years),
    }
    day["signals"] = (["heat"] if day["apparent_max_c"] is not None and day["apparent_max_c"] >= load_rules("ranking")["weather"]["heat_apparent_c"] else []) + (["rain"] if rainy * 2 > len(days) else [])
    label = f"Typical for this date (average of {min(years)}–{max(years)} records) — not a forecast"
    return _snapshot(day, basis="typical", label=label, live=False, timezone_name=timezone_name, retrieved_at=None, cached=True, source_url="https://open-meteo.com/en/docs/historical-weather-api", note="No forecast exists this far ahead.", offset=offset)


def weather_on(lat: float, lon: float, target: date, *, timezone_name: str | None = None, destination_id: str | None = None) -> dict[str, Any]:
    """Weather for a specific date, from the best source that can speak for it.

    * today … +15 days: the live forecast;
    * the past: recorded weather (recent days from the forecast API, older ones from the archive);
    * further ahead: the dataset's record for that date if one exists, otherwise the typical
      weather on that date in recent years. Each result says which one it is (``basis``).
    """
    today = local_now(timezone_name).date()
    offset = (target - today).days
    if 0 <= offset < FORECAST_DAYS:
        weather = get_weather(lat, lon, offset)
        if weather.get("status") == "ok" and (weather.get("day") or {}).get("date") == target.isoformat():
            return weather
        if weather.get("status") == "ok" and weather.get("basis") == "dataset":
            return weather
        fallback = dataset_weather_on(destination_id, target, timezone_name)
        return fallback or {"status": "unavailable", "error": weather.get("error") or {"source": "open_meteo", "code": "unavailable", "message": "Weather unavailable"}, "source": "open-meteo", "date": target.isoformat()}
    if offset < 0:
        url = OPEN_METEO_URL if -offset <= RECENT_PAST_DAYS else OPEN_METEO_ARCHIVE_URL
        try:
            raw = _fetch_daily(url, lat, lon, target, target, ARCHIVE_TTL_S if -offset > 2 else CACHE_TTL_WEATHER_S)
            daily = raw.get("daily") or {}
            if daily.get("time"):
                return _snapshot(_day_block(daily, 0), basis="observed", label="Recorded weather (Open-Meteo)", live=True, timezone_name=raw.get("timezone") or timezone_name, retrieved_at=raw.get("retrieved_at"), cached=raw.get("cached", False), source_url="https://open-meteo.com/", offset=offset)
            error = {"source": "open_meteo", "code": "no_data", "message": "No recorded weather for that date."}
        except ProviderError as exc:
            error = exc.as_dict()
        fallback = dataset_weather_on(destination_id, target, timezone_name)
        return fallback or {"status": "unavailable", "error": error, "source": "open-meteo", "date": target.isoformat()}
    stored = dataset_weather_on(destination_id, target, timezone_name)
    if stored:
        stored["note"] = "No forecast exists this far ahead; this is the dataset's record for the date."
        return stored
    typical = _typical(lat, lon, target, timezone_name, offset)
    return typical or {"status": "unavailable", "error": {"source": "open_meteo", "code": "beyond_forecast", "message": f"No forecast exists {offset} days ahead and no historical record could be retrieved."}, "source": "open-meteo", "date": target.isoformat()}
