"""Test configuration: isolated data dir, synthetic pack, and no network.

Every external provider is replaced so tests never depend on the internet,
and all behaviour is exercised against a fictional destination ("Testville")
to prove nothing depends on the demo pack.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="geoguide-tests-"))
(_TMP / "packs").mkdir()
shutil.copytree(BACKEND / "data" / "config", _TMP / "config")
shutil.copytree(BACKEND / "tests" / "fixtures" / "packs" / "testville", _TMP / "packs" / "testville")
os.environ.update({
    "DATA_DIR": str(_TMP),
    "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
    "EMBEDDINGS_ENABLED": "false",
    "GROQ_API_KEY": "",
    "SERPAPI_KEY": "",
    "AUTO_SEED_PACKS": "true",
    "AUTO_SEED_FEEDBACK": "false",
    "APP_ENV": "test",
    "LLM_QUERY_PARSING": "false",
})
sys.path.insert(0, str(BACKEND))

from app.core.http import ProviderError  # noqa: E402
from app.db.seed import load_pack  # noqa: E402
from app.db.session import init_db  # noqa: E402

init_db()
load_pack(_TMP / "packs" / "testville")

HAMPI_PACK = BACKEND / "data" / "packs" / "hampi"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fake_open_meteo(apparent_max: float = 30.0, precipitation: int = 10, code: int = 1, tz: str = "UTC") -> dict:
    today = datetime.now(timezone.utc).date()
    days = [today.fromordinal(today.toordinal() + i).isoformat() for i in range(7)]
    hours = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
    return {
        "timezone": tz,
        "current": {"time": f"{days[0]}T10:00", "temperature_2m": 28.0, "apparent_temperature": 30.0, "relative_humidity_2m": 40, "precipitation": 0.0, "weather_code": code, "wind_speed_10m": 8.0, "is_day": 1},
        "daily": {"time": days, "weather_code": [code] * 7, "temperature_2m_max": [32.0] * 7, "temperature_2m_min": [21.0] * 7, "apparent_temperature_max": [apparent_max] * 7, "precipitation_probability_max": [precipitation] * 7, "precipitation_sum": [0.0] * 7, "sunrise": [f"{d}T06:10" for d in days], "sunset": [f"{d}T18:20" for d in days], "uv_index_max": [8.0] * 7},
        "hourly": {"time": hours, "temperature_2m": [28.0] * len(hours), "apparent_temperature": [apparent_max if 12 <= int(t[11:13]) <= 15 else 27.0 for t in hours], "precipitation_probability": [precipitation] * len(hours), "weather_code": [code] * len(hours)},
        "cached": False,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail every outbound provider call explicitly (as an outage would)."""

    def offline(provider, *args, **kwargs):
        raise ProviderError(provider, "unreachable", "network disabled in tests")

    monkeypatch.setattr("app.core.http.get_json", offline)
    monkeypatch.setattr("app.geo.geocoding.get_json", offline)
    monkeypatch.setattr("app.weather.open_meteo.get_json", offline)
    monkeypatch.setattr("app.ingestion.overpass.fetch_and_store", lambda *a, **k: (0, {"source": "overpass", "code": "unreachable", "message": "offline"}))
    monkeypatch.setattr("app.services.discovery.fetch_and_store", lambda *a, **k: (0, {"source": "overpass", "code": "unreachable", "message": "offline"}))
    from app.core.cache import cache_clear_memory

    cache_clear_memory()
    yield


@pytest.fixture
def weather_ok(monkeypatch):
    def install(**kwargs):
        payload = fake_open_meteo(**kwargs)
        monkeypatch.setattr("app.weather.open_meteo._fetch", lambda lat, lon: payload)
        return payload

    return install


@pytest.fixture
def gps():
    def make(lat: float, lon: float, accuracy: float = 15.0, age_s: float = 0.0) -> dict:
        stamp = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - age_s, tz=timezone.utc).isoformat()
        return {"lat": lat, "lon": lon, "accuracy_m": accuracy, "timestamp": stamp, "source": "device"}

    return make
