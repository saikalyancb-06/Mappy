from app.ingestion.geocode import reverse_geocode
from app.ingestion.weather import fetch_weather


def test_reverse_geocode_parses_nominatim_response():
    payload = [{
        'address': {
            'city': 'Bengaluru',
            'state': 'Karnataka',
            'country': 'India',
            'country_code': 'in'
        },
        'display_name': 'Bengaluru, Karnataka, India',
        'lat': '12.9716',
        'lon': '77.5946',
        'type': 'city'
    }]

    result = reverse_geocode(payload)
    assert result['city'] == 'Bengaluru'
    assert result['country'] == 'India'
    assert result['region'] == 'Karnataka'
    assert result['lat'] == 12.9716
    assert result['lon'] == 77.5946


def test_fetch_weather_parses_open_meteo_response():
    sample = {
        'current': {'temperature_2m': 29.2, 'apparent_temperature': 31.0, 'weather_code': 1},
        'hourly': {'time': ['2026-09-23T12:00'], 'temperature_2m': [29.2], 'precipitation_probability': [10]},
        'daily': {
            'time': ['2026-09-23'],
            'temperature_2m_max': [30.4],
            'temperature_2m_min': [24.1],
            'sunset': ['2026-09-23T18:42'],
            'sunrise': ['2026-09-23T06:20']
        }
    }

    result = fetch_weather(sample)
    assert result['current']['temperature_c'] == 29.2
    assert result['daily']['max_temp_c'] == 30.4
    assert result['daily']['sunset'] == '2026-09-23T18:42'
