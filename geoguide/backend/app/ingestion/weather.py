from __future__ import annotations

from typing import Any


def fetch_weather(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload.get('current', {})
    hourly = payload.get('hourly', {})
    daily = payload.get('daily', {})
    current_temp = current.get('temperature_2m')
    current_feels_like = current.get('apparent_temperature', current_temp)
    weather_code = current.get('weather_code')
    first_hour = hourly.get('time', [None])[0]
    first_temp = (hourly.get('temperature_2m') or [None])[0]
    first_precip = (hourly.get('precipitation_probability') or [None])[0]

    return {
        'current': {
            'temperature_c': current_temp,
            'feels_like_c': current_feels_like,
            'weather_code': weather_code,
            'time': first_hour,
        },
        'hourly': {
            'time': hourly.get('time', []),
            'temperature_c': hourly.get('temperature_2m', []),
            'precipitation_probability': hourly.get('precipitation_probability', []),
        },
        'daily': {
            'date': (daily.get('time') or [None])[0],
            'max_temp_c': (daily.get('temperature_2m_max') or [None])[0],
            'min_temp_c': (daily.get('temperature_2m_min') or [None])[0],
            'sunrise': (daily.get('sunrise') or [None])[0],
            'sunset': (daily.get('sunset') or [None])[0],
            'summary': 'weather data loaded',
        },
        'next_hour': {
            'time': first_hour,
            'temperature_c': first_temp,
            'precipitation_probability': first_precip,
        },
    }
