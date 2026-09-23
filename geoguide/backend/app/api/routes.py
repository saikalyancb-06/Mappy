from __future__ import annotations

from fastapi import APIRouter

from app.ingestion.pipeline import IngestionPipeline
from app.services.agent import GeoGuideAgent
from app.services.context_manager import ContextManager
from app.services.live_context import get_live_context, safe_location
from app.services.recommender import Recommender

router = APIRouter(prefix='/api')
context_manager = ContextManager()
agent = GeoGuideAgent()
recommender = Recommender()
pipeline = IngestionPipeline()


@router.get('/health')
def health() -> dict:
    return {'status': 'ok', 'services': {'sqlite': 'ready', 'qdrant': 'pending', 'groq': 'pending'}}


@router.post('/location')
def location(payload: dict | None) -> dict:
    safe_payload = payload or {}
    try:
        lat = float(safe_payload.get('lat', 12.9716))
        lon = float(safe_payload.get('lon', 77.5946))
    except (TypeError, ValueError):
        lat, lon = 12.9716, 77.5946

    context = context_manager.build_context(location={'lat': lat, 'lon': lon, 'city': safe_payload.get('city') or 'Current area'})
    job = pipeline.start(lat, lon, safe_payload.get('accuracy'))
    return {
        'status': 'queued',
        'job_id': job.job_id,
        'area_id': context['area']['id'],
        'step': job.step,
        'progress': job.progress,
        'message': 'Location ingestion started.',
    }


@router.get('/ingestion/{job_id}/events')
def ingestion_events(job_id: str) -> dict:
    return pipeline.status(job_id)


@router.get('/now')
def now(lat: float | None = None, lon: float | None = None, city: str | None = None) -> dict:
    location = safe_location(lat, lon, city)
    live = get_live_context(**location)
    context = context_manager.build_context(location=live['location'])
    return {
        'location': context['location'],
        'area': live['area'],
        'local_time': context['local_time'],
        'weather': live['weather'],
        'daylight_left': 'unknown',
        'advisories': [],
        'briefing': 'Live place, weather, and map context is being used for this area.' if not live['provider_errors'] else 'Some live context providers are unavailable; showing the data that could be retrieved.',
        'data_status': 'live' if not live['provider_errors'] else 'partial',
        'provider_errors': live['provider_errors'],
    }


@router.get('/nearby')
def nearby(lat: float | None = None, lon: float | None = None, city: str | None = None, radius_km: float = 2.0) -> dict:
    location = safe_location(lat, lon, city)
    safe_radius = min(max(float(radius_km), 0.5), 10.0)
    live = get_live_context(**location, radius_km=safe_radius)
    ranked = recommender.rank_places({'budget': 'moderate', 'pace': 'balanced'}, live['places'])
    return {'items': ranked, 'location': live['location'], 'data_status': 'live' if not live['provider_errors'] else 'partial', 'provider_errors': live['provider_errors']}


@router.post('/ask')
def ask(payload: dict | None) -> dict:
    safe_payload = payload or {}
    question = safe_payload.get('question', 'What is good nearby?')
    location = safe_payload.get('location') or {'lat': 12.9716, 'lon': 77.5946, 'city': 'Bengaluru'}
    context = context_manager.build_context(location=location)
    return agent.answer(question, context)
