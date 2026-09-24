"""Season for a city on a date (config-driven; see data/config/seasons.json)."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.rules import load_rules
from app.geo.city import City, country_code


def season_for(city: City, day: date) -> dict[str, Any]:
    rules = load_rules("seasons")
    code = (country_code(city) or "").upper()
    calendar = rules["by_country"].get(code) or (rules["southern"] if city.lat < 0 else rules["northern"])
    name = next((season for season, months in calendar.items() if day.month in months), None)
    peak = bool(city.peak_months and day.month in city.peak_months)
    return {
        "name": name,
        "basis": f"{code} climate calendar" if code in rules["by_country"] else ("southern-hemisphere seasons" if city.lat < 0 else "northern-hemisphere seasons"),
        "peak_tourist_season": peak if city.peak_months else None,
        "peak_months": city.peak_months,
        "best_season": city.season_profile.replace("_", "-") if city.season_profile else None,
    }
