"""City enrichment: build and refresh a city's intelligence in the background.

Idempotent and partial by design. Only missing, expired or retry-due components
run; each component's result is stored as it completes, so the city is usable
(``PARTIAL``) before everything is done. Provider calls run with bounded
concurrency and a minimum interval, a run makes at most ``max_requests_per_run``
requests, and quota/authentication errors stop the run without touching stored
data. Database writes happen in one thread, in order.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from app.cities import knowledge as city_knowledge
from app.cities.registry import component_rows, components_config, evaluate_status, missing_components, set_status
from app.core.rules import load_rules
from app.db.models import CityEnrichmentComponent, Destination, IngestionJob, Poi, utcnow
from app.db.session import SessionLocal
from app.places.boundary import boundary_km
from app.places.providers.base import PlaceProviderError, PlaceResult, PlaceSearchProvider
from app.places.providers.serpapi_maps import SerpApiPlaceProvider
from app.places.reviews import store_review_signals
from app.places.store import UpsertReport, upsert_places

logger = logging.getLogger(__name__)
_running: set[str] = set()
_running_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def zoom_for(destination: Destination) -> int:
    km = boundary_km(destination)
    return 14 if km <= 5 else 13 if km <= 12 else 12 if km <= 25 else 11


def queries_for(destination: Destination, component: str) -> list[str]:
    spec = components_config()[component]
    fields = {"city": destination.name, "region": destination.region or "", "country": destination.country or ""}
    return [" ".join(template.format(**fields).split()) for template in spec.get("queries", [])]


class _Limiter:
    """At most one provider call per ``interval`` seconds across threads, and a hard cap per run."""

    def __init__(self, interval: float, budget: int) -> None:
        self.interval, self.budget, self.used = interval, budget, 0
        self._lock = threading.Lock()
        self._last = 0.0
        self.stopped: str | None = None

    def acquire(self) -> bool:
        with self._lock:
            if self.stopped or self.used >= self.budget:
                return False
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.used += 1
            return True


@dataclass
class EnrichmentReport:
    destination_id: str
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: int = 0
    discovered: int = 0
    created: int = 0
    updated: int = 0
    deduplicated: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    failed_components: list[str] = field(default_factory=list)
    duration_ms: int = 0
    stopped: str | None = None
    details_fetched: int = 0

    def add(self, upsert: UpsertReport) -> None:
        self.created += upsert.created
        self.updated += upsert.updated
        self.deduplicated += upsert.deduplicated
        for reason, count in upsert.rejected.items():
            self.rejected[reason] = self.rejected.get(reason, 0) + count

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _save_component(destination_id: str, name: str, status: str, *, items: int = 0, requests: int = 0, error: str | None = None) -> None:
    config = components_config()[name]
    days = load_rules("city_intelligence")["freshness_days"][config["class"]]
    now = _now()
    with SessionLocal() as db:
        row = db.get(CityEnrichmentComponent, f"{destination_id}:{name}") or CityEnrichmentComponent(id=f"{destination_id}:{name}", destination_id=destination_id, component=name, freshness_class=config["class"])
        row.status, row.item_count, row.requests, row.error, row.updated_at = status, items, requests, error, now
        if status != "running":
            row.last_run_at = now
            row.expires_at = now + timedelta(days=days) if status in {"done", "empty"} else None
        db.merge(row)
        db.commit()


def _update_job(job_id: str | None, **values: Any) -> None:
    if not job_id:
        return
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if job:
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = utcnow()
            db.commit()


def run_enrichment(destination_id: str, components: list[str] | None = None, *, provider: PlaceSearchProvider | None = None,
                   knowledge_collector: Callable[[Destination], tuple[int, int, dict[str, str] | None]] | None = None, job_id: str | None = None, force: bool = False) -> EnrichmentReport:
    """Collect the given (default: missing/stale) components for a city, synchronously."""
    started = time.monotonic()
    provider = provider or SerpApiPlaceProvider()
    collector = knowledge_collector or city_knowledge.collect
    report = EnrichmentReport(destination_id=destination_id)
    with SessionLocal() as db:
        destination = db.get(Destination, destination_id)
    if destination is None:
        raise ValueError(f"Unknown destination {destination_id}")
    config = components_config()
    todo = [c for c in (components or (list(config) if force else missing_components(destination))) if c in config]
    if not todo:
        set_status(destination_id, evaluate_status(destination)["status"])
        return report
    rules = load_rules("city_intelligence")["provider"]
    logger.info("city_enrichment_started destination=%s components=%s", destination_id, ",".join(todo))
    set_status(destination_id, "ENRICHING")
    for name in todo:
        _save_component(destination_id, name, "running")
    _update_job(job_id, status="running", step="collecting", progress=10)

    # 1. City knowledge documents (one source request).
    if "knowledge" in todo:
        count, requests, error = collector(destination)
        report.requests += requests
        _save_component(destination_id, "knowledge", "failed" if error else ("done" if count else "empty"), items=count, requests=requests, error=error["message"] if error else None)
        report.components["knowledge"] = {"documents": count, "error": error}
        if error:
            report.failed_components.append("knowledge")

    # 2. Place components: provider searches in parallel (bounded), writes in order.
    place_components = [c for c in todo if c != "knowledge"]
    if place_components and not provider.configured:
        for name in place_components:
            _save_component(destination_id, name, "unavailable", error="The place provider is not configured (SERPAPI_KEY).")
            report.components[name] = {"status": "unavailable"}
        report.failed_components.extend(place_components)
    elif place_components:
        limiter = _Limiter(rules["min_interval_s"], rules["max_requests_per_run"])
        zoom = zoom_for(destination)
        stop_on = set(rules["stop_on"])

        def search(query: str) -> tuple[list[PlaceResult], dict[str, str] | None, bool]:
            """(results, error, whether a provider request was made)."""
            if not limiter.acquire():
                return [], {"source": provider.name, "code": limiter.stopped or "request_budget_exhausted", "message": "Skipped: the run stopped or reached its request budget."}, False
            try:
                response = provider.search(query, lat=destination.lat, lon=destination.lon, zoom=zoom, limit=rules["results_per_query"])
                return response.results, None, not response.cached
            except PlaceProviderError as exc:
                logger.warning("serpapi_failure destination=%s code=%s", destination_id, exc.code)
                if exc.code in stop_on:
                    limiter.stopped = exc.code
                return [], exc.as_dict(), True

        plan = {name: queries_for(destination, name) for name in place_components}
        with ThreadPoolExecutor(max_workers=max(1, rules["max_concurrency"])) as pool:
            futures = {name: [pool.submit(search, q) for q in queries] for name, queries in plan.items()}
            for index, name in enumerate(place_components, start=1):
                results: list[PlaceResult] = []
                errors = []
                requests = 0
                for future in futures[name]:
                    found, error, made = future.result()
                    results.extend(found)
                    requests += int(made)
                    if error:
                        errors.append(error)
                report.requests += requests
                report.discovered += len(results)
                upsert = upsert_places(destination, results, component=name) if results else UpsertReport()
                report.add(upsert)
                stored = len(set(upsert.poi_ids))
                if stored:
                    status = "done"
                elif errors and len(errors) == len(plan[name]):
                    status = "failed"
                    report.failed_components.append(name)
                else:
                    status = "empty"
                _save_component(destination_id, name, status, items=stored, requests=requests, error=errors[0]["message"] if errors and status == "failed" else None)
                report.components[name] = {"status": status, "found": len(results), **upsert.as_dict(), "errors": errors[:2]}
                _update_job(job_id, progress=10 + int(75 * index / len(place_components)), step=f"places:{name}")
        report.stopped = limiter.stopped

        # 3. Details (hours, reviews → vibe evidence) for the most-reviewed places, within the same budget.
        if not limiter.stopped and rules.get("details_for_top"):
            with SessionLocal() as db:
                top = db.scalars(select(Poi).where(Poi.destination_id == destination_id, Poi.data_source_id == "google_maps", Poi.external_place_id.is_not(None)).order_by(Poi.review_count.desc().nullslast()).limit(rules["details_for_top"])).all()
            for poi in top:
                if not limiter.acquire():
                    break
                seed = PlaceResult(provider=provider.name, external_id=poi.external_place_id or "", data_id=poi.provider_data_id, name=poi.name, lat=poi.lat, lon=poi.lon)
                try:
                    detailed = provider.details(seed)
                except PlaceProviderError as exc:
                    if exc.code in set(rules["stop_on"]):
                        break
                    continue
                report.requests += 1
                if detailed is None:
                    continue
                report.details_fetched += 1
                report.add(upsert_places(destination, [detailed], component=None))
                if detailed.reviews:
                    store_review_signals(poi.id, detailed.reviews)

    # 4. Close the run: version, timestamps, status, caches, embeddings.
    report.duration_ms = int((time.monotonic() - started) * 1000)
    with SessionLocal() as db:
        city = db.get(Destination, destination_id)
        city.data_version = (city.data_version or 0) + 1
        city.last_refreshed_at = _now()
        if not report.failed_components:
            city.last_enriched_at = _now()
        city.last_enrichment_report = json.dumps(report.as_dict(), default=str)[:20000]
        db.commit()
        db.refresh(city)
    status = evaluate_status(city)["status"]
    set_status(destination_id, status)
    try:
        from app.knowledge.destination_pack import invalidate
        from app.retrieval.indexer import reindex_in_background

        invalidate(destination_id)
        reindex_in_background()
    except Exception:  # pragma: no cover - caches must never fail a run
        logger.warning("city_enrichment_post_steps_failed destination=%s", destination_id)
    event = "city_enrichment_failed" if report.failed_components and status in {"FAILED", "NOT_STARTED"} else "city_enrichment_completed"
    logger.info("%s destination=%s status=%s duration_ms=%d requests=%d discovered=%d created=%d updated=%d deduplicated=%d rejected=%s failed=%s",
                event, destination_id, status, report.duration_ms, report.requests, report.discovered, report.created, report.updated, report.deduplicated, report.rejected, report.failed_components)
    _update_job(job_id, status="completed" if status in {"READY", "PARTIAL"} else "failed", step="done", progress=100, error=None if status in {"READY", "PARTIAL"} else "; ".join(report.failed_components) or None)
    return report


def enqueue_enrichment(destination_id: str, *, force: bool = False, components: list[str] | None = None, runner: Callable[..., Any] | None = None) -> str | None:
    """Start a background run unless one is already running for this city. Returns the job id (or None)."""
    with _running_lock:
        if destination_id in _running:
            return None
        _running.add(destination_id)
    job_id = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(IngestionJob(id=job_id, destination_id=destination_id, status="queued", step="queued", progress=5))
        db.commit()
    set_status(destination_id, "QUEUED")

    def work() -> None:
        try:
            (runner or run_enrichment)(destination_id, components, job_id=job_id, force=force)
        except Exception as exc:  # the selection flow must never crash because enrichment did
            logger.exception("city_enrichment_failed destination=%s error=%s", destination_id, type(exc).__name__)
            for name, row in component_rows(destination_id).items():
                if row.status == "running":
                    _save_component(destination_id, name, "failed", error=f"Run stopped: {type(exc).__name__}")
            with SessionLocal() as db:
                city = db.get(Destination, destination_id)
            if city is not None:
                set_status(destination_id, evaluate_status(city)["status"] if evaluate_status(city)["status"] not in {"QUEUED", "ENRICHING"} else "FAILED")
            _update_job(job_id, status="failed", step="error", progress=100, error=type(exc).__name__)
        finally:
            with _running_lock:
                _running.discard(destination_id)

    threading.Thread(target=work, name=f"enrich-{destination_id}", daemon=True).start()
    logger.info("city_enrichment_queued destination=%s job=%s", destination_id, job_id)
    return job_id


def is_running(destination_id: str) -> bool:
    with _running_lock:
        return destination_id in _running
