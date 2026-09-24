from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.db.models import Area, IngestionJob as IngestionJobModel, Poi
from app.db.session import SessionLocal
from app.ingestion.geocode import forward_geocode, reverse_geocode
from app.ingestion.overpass import build_overpass_query, normalize_overpass_response
from app.services.knowledge import KnowledgeService


@dataclass
class IngestionJob:
    job_id: str
    status: str = 'queued'
    step: str = 'resolve_area'
    progress: int = 0


class IngestionPipeline:
    def __init__(self) -> None:
        self.jobs: dict[str, IngestionJob] = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='geoguide-ingestion')
        self.knowledge = KnowledgeService()

    def start(self, lat: float | None, lon: float | None, accuracy: float | None = None, city: str | None = None) -> IngestionJob:
        import uuid

        safe_lat = float(lat) if lat is not None else 0.0
        safe_lon = float(lon) if lon is not None else 0.0
        if not (-90 <= safe_lat <= 90 and -180 <= safe_lon <= 180):
            safe_lat, safe_lon = 0.0, 0.0

        job = IngestionJob(job_id=str(uuid.uuid4()), status='running', step='resolve_area', progress=10)
        self.jobs[job.job_id] = job
        with SessionLocal() as db:
            db.add(IngestionJobModel(id=job.job_id, area_id=f'pending-{job.job_id}', status=job.status, step=job.step, progress=job.progress))
            db.commit()
        self.executor.submit(self.run, job.job_id, safe_lat if lat is not None else None, safe_lon if lon is not None else None, city)
        return job

    def _update(self, job_id: str, **values: Any) -> None:
        with SessionLocal() as db:
            stored = db.get(IngestionJobModel, job_id)
            if stored:
                for key, value in values.items():
                    setattr(stored, key, value)
                db.commit()

    def run(self, job_id: str, lat: float | None, lon: float | None, city: str | None = None) -> None:
        try:
            self._update(job_id, status='running', step='resolve_city', progress=15, error=None)
            with httpx.Client(timeout=12.0, headers={'User-Agent': 'GeoGuide/0.1 local place companion'}) as client:
                resolved = forward_geocode(city, client) if city else None
                if resolved is None and lat is not None and lon is not None:
                    resolved = reverse_geocode(self._get_json(client, 'https://nominatim.openstreetmap.org/reverse', lat=lat, lon=lon, format='jsonv2', zoom=14))
                if not resolved or resolved.get('lat') is None or resolved.get('lon') is None:
                    raise ValueError('The destination could not be resolved to coordinates.')
                resolved_lat = float(resolved['lat'])
                resolved_lon = float(resolved['lon'])
                area_id = self._area_id(resolved)
                self._update(job_id, area_id=area_id, step='discover_sources', progress=25)
                area = Area(id=area_id, name=resolved.get('city'), country=resolved.get('country'), region=resolved.get('region'), lat=resolved_lat, lon=resolved_lon)
                with SessionLocal() as db:
                    if db.get(Area, area_id) is None:
                        db.add(area)
                    db.commit()
                self._update(job_id, step='collect_documents', progress=40)
                payload = self._get_json(client, 'https://overpass-api.de/api/interpreter', data=build_overpass_query(resolved_lat, resolved_lon, 10.0))
                places = normalize_overpass_response(payload)
                self._update(job_id, step='normalize_entities', progress=65)
                with SessionLocal() as db:
                    for place in places:
                        if place.get('lat') is None or place.get('lon') is None:
                            continue
                        poi_id = f'osm-{area_id}-{place.get("id")}'
                        existing = db.get(Poi, poi_id)
                        values = {'area_id': area_id, 'name': place['name'], 'category': place['category'], 'lat': float(place['lat']), 'lon': float(place['lon']), 'source': 'OpenStreetMap', 'opening_hours': (place.get('tags') or {}).get('opening_hours')}
                        if existing:
                            for key, value in values.items():
                                setattr(existing, key, value)
                        else:
                            db.add(Poi(id=poi_id, **values))
                    db.commit()
                self._update(job_id, step='generate_embeddings', progress=82)
                self.knowledge.index_places(places, area_id)
                self._update(job_id, step='finalize', progress=100, status='completed')
        except Exception as exc:
            self._update(job_id, status='failed', step='error', error=str(exc))

    @staticmethod
    def _get_json(client: httpx.Client, url: str, **params: Any) -> Any:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _area_id(resolved: dict[str, Any]) -> str:
        key = '|'.join(str(resolved.get(value) or '').strip().lower() for value in ('city', 'region', 'country'))
        return f'area-{sha1(key.encode("utf-8")).hexdigest()[:16]}'

    def status(self, job_id: str) -> dict[str, Any]:
        with SessionLocal() as db:
            stored = db.get(IngestionJobModel, job_id)
            if not stored:
                return {'status': 'not_found'}
            return {'job_id': stored.id, 'status': stored.status, 'step': stored.step, 'progress': stored.progress, 'error': stored.error}
