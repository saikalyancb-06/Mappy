"""Generic destination data-pack importer.

A pack is a directory (see data/packs/*) with ``pack.json`` (provenance),
``destination.json`` and optional ``pois.json``, ``place_kb.json``,
``poi_facts.json``, ``safety_advisories.json`` and ``events.json``. Any
destination can be added by dropping in a pack; no code changes are needed.

Usage: ``python -m app.db.seed [--pack DIR ...]`` (defaults to every pack in PACKS_DIR).
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from app.config import PACKS_DIR
from app.core.text import normalize
from app.db.models import DataSource, Destination, EntityAlias, EventFestival, KnowledgeChunk, Poi, SafetyAdvisory, utcnow
from app.db.session import SessionLocal, init_db
from app.geo.distance import valid_coordinates

logger = logging.getLogger(__name__)
CHUNK_CHARS = 700


def _read(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on sentence boundaries into chunks of at most ~max_chars."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _json_or_none(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def load_pack(pack_dir: Path) -> dict[str, int]:
    meta = _read(pack_dir / "pack.json") or {}
    destination_data = _read(pack_dir / "destination.json")
    if not destination_data:
        raise ValueError(f"{pack_dir} has no destination.json")
    if not valid_coordinates(destination_data.get("lat"), destination_data.get("lon")):
        raise ValueError(f"{pack_dir}/destination.json has invalid coordinates")
    source_id = meta.get("id") or f"pack-{pack_dir.name}"
    default_source = f"curated: {meta.get('name') or pack_dir.name}"
    destination_id = destination_data["id"]
    counts = {"pois": 0, "chunks": 0, "advisories": 0, "events": 0, "skipped": 0}

    with SessionLocal() as db:
        # Re-importing a pack replaces its rows (idempotent), preserving other data.
        for model in (Poi, KnowledgeChunk, SafetyAdvisory, EventFestival):
            db.execute(delete(model).where(model.data_source_id == source_id))
        db.execute(delete(EntityAlias).where(EntityAlias.entity_id == destination_id))
        db.merge(DataSource(id=source_id, name=meta.get("name") or pack_dir.name, description=meta.get("description"), license=meta.get("license"), url=(meta.get("sources") or [{}])[0].get("url"), collected_at=meta.get("collected_at"), geographic_coverage=meta.get("geographic_coverage"), confidence=meta.get("confidence"), version=meta.get("version"), imported_at=utcnow()))
        db.merge(Destination(
            id=destination_id,
            name=destination_data["name"],
            region=destination_data.get("region"),
            country=destination_data.get("country"),
            country_code=destination_data.get("country_code"),
            lat=float(destination_data["lat"]),
            lon=float(destination_data["lon"]),
            coverage_radius_km=float(destination_data.get("coverage_radius_km") or 10),
            timezone=destination_data.get("timezone"),
            currency=destination_data.get("currency"),
            languages=json.dumps(destination_data.get("languages") or []),
            summary=destination_data.get("summary"),
            source=destination_data.get("source") or default_source,
            source_url=destination_data.get("source_url"),
            data_source_id=source_id,
            curated=True,
            updated_at=utcnow(),
        ))
        seen_aliases: set[str] = set()
        for alias in [destination_data["name"], *(destination_data.get("aliases") or [])]:
            key = normalize(alias)
            if key and key not in seen_aliases:
                seen_aliases.add(key)
                db.add(EntityAlias(entity_type="destination", entity_id=destination_id, alias=alias, normalized=key))
        db.flush()

        poi_ids: set[str] = set()
        for item in _read(pack_dir / "pois.json") or []:
            if not valid_coordinates(item.get("lat"), item.get("lon")) or not item.get("name"):
                counts["skipped"] += 1
                logger.warning("pack_poi_skipped pack=%s id=%s reason=invalid", pack_dir.name, item.get("id"))
                continue
            poi_id = item["id"]
            poi_ids.add(poi_id)
            db.execute(delete(EntityAlias).where(EntityAlias.entity_type == "poi", EntityAlias.entity_id == poi_id))
            db.merge(Poi(
                id=poi_id,
                destination_id=item.get("destination_id") or destination_id,
                name=item["name"],
                normalized_name=normalize(item["name"]),
                kind=item.get("kind") or "attraction",
                category=item.get("category"),
                tags=json.dumps(item.get("tags") or []),
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                coordinate_precision=item.get("coordinate_precision"),
                address=item.get("address"),
                neighborhood=item.get("neighborhood"),
                description=item.get("description"),
                opening_hours=_json_or_none(item.get("opening_hours")),
                opening_hours_raw=item.get("opening_hours_raw"),
                entry_fee=item.get("entry_fee"),
                entry_cost=f"{Decimal(str(item['entry_fee'])):.2f}" if item.get("entry_fee") is not None else None,
                entry_fee_foreign=item.get("entry_fee_foreign"),
                fee_currency=item.get("fee_currency") or (destination_data.get("currency") if item.get("entry_fee") is not None else None),
                fee_notes=item.get("fee_notes"),
                price_level=item.get("price_level"),
                visit_duration_min=item.get("visit_duration_min"),
                rating=item.get("rating"),
                review_count=item.get("review_count"),
                step_free=item.get("step_free"),
                accessibility_notes=item.get("accessibility_notes"),
                indoor=item.get("indoor"),
                walking_effort=item.get("walking_effort"),
                best_time=item.get("best_time"),
                phone=item.get("phone"),
                website=item.get("website"),
                source=item.get("source") or default_source,
                source_url=item.get("source_url"),
                source_id=item.get("source_id"),
                data_source_id=source_id,
                confidence=item.get("confidence"),
                fetched_at=utcnow(),
                updated_at=utcnow(),
            ))
            aliases_seen: set[str] = set()
            for alias in [item["name"], *(item.get("aliases") or [])]:
                key = normalize(alias)
                if key and key not in aliases_seen:
                    aliases_seen.add(key)
                    db.add(EntityAlias(entity_type="poi", entity_id=poi_id, alias=alias, normalized=key))
            if item.get("description"):
                db.merge(KnowledgeChunk(id=f"{poi_id}:description", document_id=f"{poi_id}:description", destination_id=destination_id, poi_id=poi_id, kind="poi_fact", category="description", title=item["name"], content=f"{item['name']}: {item['description']}", source=item.get("source") or default_source, source_url=item.get("source_url"), data_source_id=source_id, confidence=item.get("confidence"), updated_at=utcnow()))
                counts["chunks"] += 1
            counts["pois"] += 1

        for document in _read(pack_dir / "place_kb.json") or []:
            for index, content in enumerate(chunk_text(document["content"])):
                db.merge(KnowledgeChunk(id=f"{document['id']}:{index}", document_id=document["id"], destination_id=destination_id, poi_id=document.get("poi_id"), kind="place_kb", category=document.get("category"), title=document.get("title"), content=content, language=document.get("language") or "en", source=document.get("source") or default_source, source_url=document.get("source_url"), data_source_id=source_id, confidence=document.get("confidence", meta.get("confidence")), updated_at=utcnow()))
                counts["chunks"] += 1

        for fact in _read(pack_dir / "poi_facts.json") or []:
            if fact.get("poi_id") and fact["poi_id"] not in poi_ids:
                counts["skipped"] += 1
                continue
            db.merge(KnowledgeChunk(id=fact["id"], document_id=fact["id"], destination_id=destination_id, poi_id=fact.get("poi_id"), kind="poi_fact", category=fact.get("category"), title=fact.get("title"), content=fact["content"], language=fact.get("language") or "en", source=fact.get("source") or default_source, source_url=fact.get("source_url"), data_source_id=source_id, confidence=fact.get("confidence", meta.get("confidence")), updated_at=utcnow()))
            counts["chunks"] += 1

        for advisory in _read(pack_dir / "safety_advisories.json") or []:
            db.merge(SafetyAdvisory(id=advisory["id"], destination_id=advisory.get("destination_id") or destination_id, poi_id=advisory.get("poi_id"), title=advisory["title"], body=advisory.get("body"), category=advisory.get("category"), severity=advisory.get("severity") or "info", active_months=_json_or_none(advisory.get("active_months")), valid_from=advisory.get("valid_from"), valid_to=advisory.get("valid_to"), source=advisory.get("source") or default_source, source_url=advisory.get("source_url"), data_source_id=source_id, updated_at=utcnow()))
            counts["advisories"] += 1

        for item in _read(pack_dir / "events.json") or []:
            db.merge(EventFestival(id=item["id"], destination_id=item.get("destination_id") or destination_id, title=item["title"], summary=item.get("summary"), start_date=item.get("start_date"), end_date=item.get("end_date"), typical_months=_json_or_none(item.get("typical_months")), recurrence=item.get("recurrence"), source=item.get("source") or default_source, source_url=item.get("source_url"), data_source_id=source_id, confidence=item.get("confidence"), updated_at=utcnow()))
            counts["events"] += 1
        db.commit()
    logger.info("pack_loaded pack=%s counts=%s", pack_dir.name, counts)
    return counts


def available_packs() -> list[Path]:
    if not PACKS_DIR.exists():
        return []
    return sorted(path for path in PACKS_DIR.iterdir() if (path / "destination.json").exists())


def seed_if_empty() -> dict[str, Any]:
    with SessionLocal() as db:
        if db.query(Destination).filter(Destination.curated.is_(True)).count():
            return {"status": "already_seeded"}
    loaded = {}
    for pack in available_packs():
        try:
            loaded[pack.name] = load_pack(pack)
        except Exception as exc:  # a broken pack must not stop the API
            logger.error("pack_load_failed pack=%s error=%s", pack.name, exc)
            loaded[pack.name] = {"error": str(exc)}
    return {"status": "seeded", "packs": loaded}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import GeoGuide destination data packs")
    parser.add_argument("--pack", action="append", help="pack directory (repeatable); default: all packs")
    args = parser.parse_args()
    init_db()
    packs = [Path(p) for p in args.pack] if args.pack else available_packs()
    for pack in packs:
        print(pack.name, load_pack(pack))
