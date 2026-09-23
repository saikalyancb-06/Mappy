from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.models import IngestionJob as IngestionJobModel
from app.db.session import SessionLocal


@dataclass
class IngestionJob:
    job_id: str
    status: str = 'queued'
    step: str = 'resolve_area'
    progress: int = 0


class IngestionPipeline:
    def __init__(self) -> None:
        self.jobs: dict[str, IngestionJob] = {}

    def start(self, lat: float | None, lon: float | None, accuracy: float | None = None) -> IngestionJob:
        import uuid

        safe_lat = float(lat) if lat is not None else 12.9716
        safe_lon = float(lon) if lon is not None else 77.5946
        if not (-90 <= safe_lat <= 90 and -180 <= safe_lon <= 180):
            safe_lat, safe_lon = 12.9716, 77.5946

        job = IngestionJob(job_id=str(uuid.uuid4()), status='running', step='resolve_area', progress=10)
        self.jobs[job.job_id] = job
        with SessionLocal() as db:
            db.add(IngestionJobModel(id=job.job_id, area_id=f'area-{safe_lat}-{safe_lon}', status=job.status, step=job.step, progress=job.progress))
            db.commit()
        return job

    def status(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            with SessionLocal() as db:
                stored = db.get(IngestionJobModel, job_id)
                if not stored:
                    return {'status': 'not_found'}
                return {'job_id': stored.id, 'status': stored.status, 'step': stored.step, 'progress': stored.progress}
        return {'job_id': job.job_id, 'status': job.status, 'step': job.step, 'progress': job.progress}
