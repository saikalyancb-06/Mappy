"""Idempotent persistence of provider places into ``pois``.

Identity, in order: the provider's place id → an existing row with the same
external id → the same name nearby (normalised name + distance) → the same
address. Re-running enrichment updates rows instead of creating new ones, and a
place found by several queries is stored once. Curated and dataset rows are
never overwritten by provider data: only their missing fields are filled in.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.rules import load_rules
from app.core.text import name_similarity, normalize
from app.db.models import Destination, Poi, ProviderRawRecord
from app.db.session import SessionLocal
from app.geo.distance import haversine_km
from app.places import boundary
from app.places.normalize import poi_values
from app.places.providers.base import PlaceResult

logger = logging.getLogger(__name__)
PROVIDER_OWNED = {"google_maps"}
REFRESHABLE = ("rating", "review_count", "opening_hours", "opening_hours_raw", "phone", "website", "images", "status", "provider_types", "subcategory", "metadata_json", "price_level", "description", "address", "provider_data_id")
FILL_ONLY = ("address", "phone", "website", "opening_hours", "opening_hours_raw", "rating", "review_count", "description", "images", "provider_types", "subcategory", "price_level")


@dataclass
class UpsertReport:
    created: int = 0
    updated: int = 0
    deduplicated: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    poi_ids: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "updated": self.updated, "deduplicated": self.deduplicated, "rejected": self.rejected, "stored": len(set(self.poi_ids))}


def poi_id_for(destination_id: str, external_id: str) -> str:
    """Deterministic: the same provider place in the same city always has the same id."""
    return "gm-" + hashlib.sha1(f"{destination_id}|{external_id}".encode()).hexdigest()[:16]


def _numbers(name: str) -> set[str]:
    return {token for token in name.split() if any(ch.isdigit() for ch in token)}


def _address_key(address: str | None) -> str:
    return normalize(address or "")


def _match(existing: list[Poi], values: dict[str, Any]) -> tuple[Poi | None, str]:
    rules = load_rules("city_intelligence")["places"]
    external = values["external_place_id"]
    for row in existing:
        if row.external_place_id and row.external_place_id == external:
            return row, "external_id"
    name = values["normalized_name"]
    address = _address_key(values.get("address"))
    best: tuple[float, Poi] | None = None
    for row in existing:
        if _numbers(row.normalized_name) != _numbers(name):
            continue  # "Terminal 1" and "Terminal 2" are different places, however similar the names
        distance = haversine_km(row.lat, row.lon, values["lat"], values["lon"])
        same_name = row.normalized_name == name and distance <= rules["dedup_same_name_distance_km"]
        similar = distance <= rules["dedup_distance_km"] and name_similarity(name, row.normalized_name) >= rules["dedup_name_similarity"]
        # A shared address alone is not identity (a mall holds many places): the names must match too.
        same_address = bool(address) and distance <= rules["dedup_same_name_distance_km"] and name_similarity(address, _address_key(row.address)) >= rules["address_similarity"] and name_similarity(name, row.normalized_name) >= 0.85
        if same_name or similar or same_address:
            if best is None or distance < best[0]:
                best = (distance, row)
    return (best[1], "name_distance_address") if best else (None, "")


def upsert_places(destination: Destination, places: list[PlaceResult], *, component: str | None = None, keep_raw: bool = True) -> UpsertReport:
    report = UpsertReport()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        existing = list(db.scalars(select(Poi).where(Poi.destination_id == destination.id)).all())
        raw: dict[str, tuple[PlaceResult, str]] = {}
        for place in places:
            values = poi_values(place, component=component, now=now)
            if values is None:
                report.reject("missing_coordinates")
                continue
            inside, reason = boundary.check(destination, values["lat"], values["lon"], values.get("address"))
            if not inside:
                report.reject(reason)
                continue
            row, how = _match(existing, values)
            if row is None:
                row = Poi(id=poi_id_for(destination.id, values["external_place_id"]), destination_id=destination.id, tags=json.dumps([]), **values)
                db.add(row)
                existing.append(row)
                report.created += 1
                logger.info("poi_created destination=%s poi=%s", destination.id, row.id)
            elif how == "external_id" and (row.data_source_id or "") in PROVIDER_OWNED:
                # Same provider place: refresh what the provider owns (ratings, hours, contact, status).
                for key in REFRESHABLE:
                    if values.get(key) is not None:
                        setattr(row, key, values[key])
                row.last_verified_at, row.expires_at, row.updated_at = values["last_verified_at"], values["expires_at"], now
                report.updated += 1
                logger.info("poi_updated destination=%s poi=%s", destination.id, row.id)
            else:
                # The same place under another identity, or a curated/dataset/OSM row: link and fill gaps only.
                for key in FILL_ONLY:
                    if getattr(row, key, None) in (None, "", "[]") and values.get(key) is not None:
                        setattr(row, key, values[key])
                row.external_place_id = row.external_place_id or values["external_place_id"]
                row.provider_data_id = row.provider_data_id or values.get("provider_data_id")
                row.last_verified_at, row.updated_at = now, now
                report.deduplicated += 1
                logger.info("poi_deduplicated destination=%s poi=%s by=%s", destination.id, row.id, how)
            report.poi_ids.append(row.id)
            if keep_raw and place.raw:
                raw[f"{place.provider}:{place.external_id}"] = (place, row.id)  # one audit row per place, even if found twice
        for raw_id, (found, poi_id) in raw.items():
            db.merge(ProviderRawRecord(id=raw_id, provider=found.provider, external_id=found.external_id, poi_id=poi_id, payload=json.dumps(found.raw, default=str)[:200000], fetched_at=now))
        db.commit()
    return report
