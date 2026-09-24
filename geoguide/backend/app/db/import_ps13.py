"""Importer for the organiser-provided PS-13 dataset (data/sources/ps13/PS-13.db).

Additive and provenance-preserving (dataset rules R1–R8):
* original IDs are kept as our primary keys (never re-issued or parsed);
* money stays exact decimal text next to its ISO-4217 currency;
* rows whose status is not ``active`` are not shown;
* a dataset city that matches a curated destination is attached to it, so
  curated and dataset evidence sit side by side and are deduplicated at
  query time (curated data wins on conflicting fields).

Usage: ``python -m app.db.import_ps13 [--db PATH]``. The API runs it at start-up
when the dataset is present and has not been imported yet.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from app.config import PS13_DB_PATH
from app.core.rules import load_rules, taxonomy
from app.core.text import name_similarity, normalize
from app.events.store import classify as classify_event
from app.db.models import DataSource, Destination, EntityAlias, EventFestival, KnowledgeChunk, Poi, SafetyAdvisory, WeatherDaily, utcnow
from app.db.session import SessionLocal, init_db
from app.geo.distance import haversine_km, valid_coordinates
from app.geo.opening_hours import DAYS

logger = logging.getLogger(__name__)


def _decimal_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _opening_hours(opens: str | None, closes: str | None, closed_days: str | None) -> dict[str, Any] | None:
    """Structured hours only when both ends are known; a missing end means unknown hours."""
    if not opens or not closes:
        return None
    closed = []
    for part in (closed_days or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            closed.append(DAYS[int(part)])  # dataset convention: 0 = Monday
    hours: dict[str, Any] = {"weekly": {"daily": [[opens, closes]]}}
    if closed:
        hours["closed"] = closed
    return hours


def _category(mapping: dict[str, Any], code: str | None, poi_category: str | None) -> str:
    codes = mapping["category_codes"]
    return codes.get(code or "") or codes.get(poi_category or "") or "monument"


def _tags(raw: str | None, mapping: dict[str, Any]) -> list[str]:
    tags = [t.strip() for t in (raw or "").split(",") if t.strip()]
    extra = [mapping["tag_map"][t] for t in tags if t in mapping["tag_map"]]
    return sorted(set(tags + extra))


def _match_destination(db, name: str, lat: float, lon: float, rules: dict[str, Any]) -> Destination | None:
    for destination in db.scalars(select(Destination)).all():
        if name_similarity(name, destination.name) >= rules["min_name_similarity"] and haversine_km(lat, lon, destination.lat, destination.lon) <= rules["max_distance_km"]:
            return destination
    return None


def import_ps13(db_path: Path | str | None = None) -> dict[str, int]:
    path = Path(db_path or PS13_DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"PS-13 dataset not found at {path}")
    mapping = load_rules("ps13_mapping")
    meta = mapping["source"]
    source_id = meta["id"]
    source_label = meta["name"]
    confidence = float(meta["confidence"])
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rows = lambda sql: src.execute(sql).fetchall()  # noqa: E731
    counts = {"destinations": 0, "pois": 0, "hotels": 0, "chunks": 0, "advisories": 0, "events": 0, "weather_days": 0, "skipped": 0}
    category_codes = {row["category_id"]: row["code"] for row in rows("SELECT category_id, code FROM categories")}

    with SessionLocal() as db:
        for model in (Poi, KnowledgeChunk, SafetyAdvisory, EventFestival, WeatherDaily):
            db.execute(delete(model).where(model.data_source_id == source_id))
        db.merge(DataSource(id=source_id, name=source_label, description="Organiser-provided travel dataset for PS-13 (19 tables, synthetic).", license=meta["license"], collected_at="2026-08-18", geographic_coverage="60 cities (India and nearby countries)", confidence=confidence, version=meta["version"], imported_at=utcnow()))

        # ---- cities → destinations (attach to curated destinations when they are the same place)
        city_to_destination: dict[str, str] = {}
        spread: dict[str, float] = {}
        countries = {row["country_id"]: row for row in rows("SELECT * FROM countries")}
        for city in rows("SELECT * FROM cities WHERE status = 'active'"):
            country = countries.get(city["country_id"])
            lat, lon = float(city["lat"]), float(city["lng"])
            existing = _match_destination(db, city["name"], lat, lon, mapping["merge_destination"])
            if existing:
                city_to_destination[city["city_id"]] = existing.id
                existing.population = existing.population or city["population"]
                existing.peak_months = existing.peak_months or json.dumps([int(m) for m in city["peak_months"].split(",") if m.strip().isdigit()])
                existing.season_profile = existing.season_profile or city["season_profile"]
                continue
            db.merge(Destination(
                id=city["city_id"], name=city["name"], region=city["state"], country=country["name"] if country else None, country_code=city["country_code"],
                currency=country["default_currency"] if country else None,
                lat=lat, lon=lon, coverage_radius_km=float(mapping["coverage_radius_km"]["min"]), timezone=city["timezone"],
                languages=json.dumps([city["primary_language"]]), summary=city["description"], population=city["population"],
                peak_months=json.dumps([int(m) for m in city["peak_months"].split(",") if m.strip().isdigit()]), season_profile=city["season_profile"],
                source=source_label, data_source_id=source_id, curated=False, updated_at=utcnow(),
            ))
            city_to_destination[city["city_id"]] = city["city_id"]
            counts["destinations"] += 1
        db.flush()
        centres = {d.id: (d.lat, d.lon) for d in db.scalars(select(Destination)).all()}
        currencies = {row["country_code"]: row["default_currency"] for row in rows("SELECT c.country_code, n.default_currency FROM cities c JOIN countries n ON n.country_id = c.country_id")}

        def track(destination_id: str, lat: float, lon: float) -> None:
            centre = centres.get(destination_id)
            if centre:
                spread[destination_id] = max(spread.get(destination_id, 0.0), haversine_km(centre[0], centre[1], lat, lon))

        # ---- activities_poi → pois
        poi_ids: set[str] = set()
        for row in rows("SELECT * FROM activities_poi WHERE status = 'active'"):
            destination_id = city_to_destination.get(row["city_id"])
            if not destination_id or not valid_coordinates(row["lat"], row["lng"]):
                counts["skipped"] += 1
                continue
            category = _category(mapping, category_codes.get(row["category_id"]), row["poi_category"])
            hours = _opening_hours(row["opens_at"], row["closes_at"], row["closed_days"])
            raw_hours = None if hours else " – ".join(filter(None, [f"opens {row['opens_at']}" if row["opens_at"] else None, f"closes {row['closes_at']}" if row["closes_at"] else None])) or None
            cost = _decimal_text(row["entry_cost"])
            accessibility = row["accessibility"]
            db.merge(Poi(
                id=row["poi_id"], destination_id=destination_id, name=row["name"], normalized_name=normalize(row["name"]),
                kind=taxonomy()["categories"].get(category, {}).get("kind", "attraction"), category=category,
                tags=json.dumps(_tags(row["tags"], mapping)), lat=float(row["lat"]), lon=float(row["lng"]), coordinate_precision="dataset",
                description=row["description"], opening_hours=json.dumps(hours) if hours else None, opening_hours_raw=raw_hours,
                entry_cost=cost, entry_fee=float(cost) if cost is not None else None, fee_currency=row["currency"],
                price_level=None if cost is None else (0 if Decimal(cost) == 0 else 1 if Decimal(cost) <= 200 else 2 if Decimal(cost) <= 750 else 3),
                visit_duration_min=row["typical_duration_minutes"], popularity_score=row["popularity_score"], value_score=row["value_score"], carbon_kg=float(row["carbon_kg"]),
                step_free=True if accessibility == "step_free" else False, accessibility_notes={"partial": "Partially accessible", "none": "Not accessible"}.get(accessibility),
                indoor=True if "indoor" in (row["tags"] or "") and "outdoor" not in (row["tags"] or "") else (False if "outdoor" in (row["tags"] or "") else None),
                best_time=row["best_season"], source=source_label, source_id=row["poi_id"], data_source_id=source_id, confidence=confidence,
                status="active", fetched_at=utcnow(), updated_at=utcnow(),
            ))
            poi_ids.add(row["poi_id"])
            track(destination_id, float(row["lat"]), float(row["lng"]))
            counts["pois"] += 1

        # ---- hotels → pois (kind=stay). The dataset carries no room rates, so no price is invented.
        for row in rows("SELECT * FROM hotels WHERE status = 'active'"):
            destination_id = city_to_destination.get(row["city_id"])
            if not destination_id or not valid_coordinates(row["lat"], row["lng"]):
                counts["skipped"] += 1
                continue
            score = float(row["guest_score"]) if row["guest_score"] is not None else None
            db.merge(Poi(
                id=row["hotel_id"], destination_id=destination_id, name=row["name"], normalized_name=normalize(row["name"]), kind="stay", category="hotel",
                tags=json.dumps([row["property_type"]]), lat=float(row["lat"]), lon=float(row["lng"]), coordinate_precision="dataset",
                address=row["address_line"], description=row["description"], opening_hours=json.dumps({"always_open": True}),
                star_rating=row["star_rating"], guest_score=score, rating=round(score / 2, 2) if score is not None else None, review_count=row["review_count"],
                property_type=row["property_type"], checkin_time=row["checkin_time"], checkout_time=row["checkout_time"],
                distance_to_centre_km=float(row["distance_to_centre_km"]), fee_currency=row["base_currency"],
                price_level=min(4, max(1, int(row["star_rating"]) - 1)) if row["star_rating"] else None,
                source=source_label, source_id=row["hotel_id"], data_source_id=source_id, confidence=confidence, status="active", fetched_at=utcnow(), updated_at=utcnow(),
            ))
            track(destination_id, float(row["lat"]), float(row["lng"]))
            counts["hotels"] += 1

        # Dataset POIs of a new destination can lie far from its centre: size coverage to fit them.
        cov = mapping["coverage_radius_km"]
        for destination in db.scalars(select(Destination).where(Destination.data_source_id == source_id)).all():
            destination.coverage_radius_km = round(min(cov["max"], max(cov["min"], spread.get(destination.id, 0.0) + cov["padding"])), 1)

        # ---- knowledge
        for row in rows("SELECT * FROM place_kb"):
            destination_id = city_to_destination.get(row["city_id"]) if row["city_id"] else None
            db.merge(KnowledgeChunk(id=row["chunk_id"], document_id=row["chunk_id"], destination_id=destination_id, poi_id=row["poi_id"] if row["poi_id"] in poi_ids else None, kind="place_kb", category=row["section"], title=row["title"], content=row["body"], language=row["language"], source=f"{row['source_label']} ({source_label})", data_source_id=source_id, confidence=confidence, updated_at=utcnow()))
            counts["chunks"] += 1
        poi_destination = {pid: did for pid, did in db.execute(select(Poi.id, Poi.destination_id).where(Poi.id.in_(list(poi_ids)))).all()}
        for row in rows("SELECT * FROM poi_facts_kb"):
            if row["poi_id"] not in poi_ids:
                counts["skipped"] += 1
                continue
            db.merge(KnowledgeChunk(id=row["fact_id"], document_id=row["fact_id"], destination_id=poi_destination.get(row["poi_id"]), poi_id=row["poi_id"], kind="poi_fact", category=row["fact_type"], content=row["fact_text"], language=row["language"], source=source_label, data_source_id=source_id, confidence=mapping["fact_confidence"].get(row["confidence"], 0.5), updated_at=utcnow()))
            counts["chunks"] += 1

        # ---- safety advisories: severity kept exactly as issued
        for row in rows("SELECT * FROM safety_advisories WHERE status = 'active'"):
            destination_id = city_to_destination.get(row["city_id"])
            if not destination_id:
                continue
            db.merge(SafetyAdvisory(id=row["advisory_id"], destination_id=destination_id, title=row["title"], body=row["body"], category=row["advisory_type"], severity=row["level"], valid_from=row["valid_from"], valid_to=row["valid_to"], language=row["language"], issuing_body=row["issuing_body"], affected_area=row["affected_area"], source=f"{row['issuing_body']} ({source_label})", source_url=row["source_url"], data_source_id=source_id, updated_at=utcnow()))
            counts["advisories"] += 1

        # ---- events
        event_categories = {r["category_id"]: r["code"] for r in rows("SELECT category_id, code FROM categories")}
        for row in rows("SELECT * FROM events_festivals WHERE status = 'active'"):
            destination_id = city_to_destination.get(row["city_id"])
            if not destination_id:
                continue
            event_type, event_category = classify_event(row["name"], row["description"], event_categories.get(row["category_id"]), recurring=row["recurrence"] in {"annual", "biennial"})
            db.merge(EventFestival(
                id=row["event_id"], destination_id=destination_id, title=row["name"], summary=row["description"], start_date=row["start_date"], end_date=row["end_date"],
                recurrence=row["recurrence"], is_ticketed=bool(row["is_ticketed"]), ticket_price=_decimal_text(row["ticket_price"]), currency=row["currency"],
                venue_lat=float(row["venue_lat"]) if row["venue_lat"] is not None else None, venue_lon=float(row["venue_lng"]) if row["venue_lng"] is not None else None,
                source=source_label, data_source_id=source_id, confidence=confidence, updated_at=utcnow(),
                event_type=event_type, category=event_category, season=row["season"], expected_footfall=row["expected_footfall"], status=row["status"], last_verified_at=row["updated_at"],
            ))
            counts["events"] += 1

        # ---- daily weather (fallback when live weather is unavailable)
        for row in rows("SELECT * FROM weather_daily"):
            destination_id = city_to_destination.get(row["city_id"])
            if not destination_id:
                continue
            db.merge(WeatherDaily(id=row["weather_id"], destination_id=destination_id, for_date=row["for_date"], temp_min_c=row["temp_min_c"], temp_max_c=row["temp_max_c"], feels_like_c=row["feels_like_c"], precipitation_mm=row["precipitation_mm"], humidity_pct=row["humidity_pct"], wind_kph=row["wind_kph"], condition=row["condition"], is_extreme=bool(row["is_extreme"]), data_source_id=source_id, updated_at=utcnow()))
            counts["weather_days"] += 1

        # Aliases so dataset IDs and names resolve like any other destination.
        for city_id, destination_id in city_to_destination.items():
            key = normalize(city_id)
            if not db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "destination", EntityAlias.entity_id == destination_id, EntityAlias.normalized == key)).first():
                db.add(EntityAlias(entity_type="destination", entity_id=destination_id, alias=city_id, normalized=key))
        for destination_id in set(city_to_destination.values()):
            destination = db.get(Destination, destination_id)
            key = normalize(destination.name)
            if not db.scalars(select(EntityAlias).where(EntityAlias.entity_type == "destination", EntityAlias.entity_id == destination_id, EntityAlias.normalized == key)).first():
                db.add(EntityAlias(entity_type="destination", entity_id=destination_id, alias=destination.name, normalized=key))
        db.commit()
    src.close()
    logger.info("ps13_imported counts=%s", counts)
    return counts


def import_if_needed() -> dict[str, Any]:
    if not Path(PS13_DB_PATH).exists():
        return {"status": "dataset_missing"}
    with SessionLocal() as db:
        if db.get(DataSource, load_rules("ps13_mapping")["source"]["id"]):
            return {"status": "already_imported"}
    try:
        return {"status": "imported", "counts": import_ps13()}
    except Exception as exc:  # a broken dataset must not stop the API
        logger.error("ps13_import_failed error=%s", exc)
        return {"status": "failed", "error": str(exc)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import the organiser PS-13 dataset")
    parser.add_argument("--db", help="path to PS-13.db (default: data/sources/ps13/PS-13.db)")
    args = parser.parse_args()
    init_db()
    print(import_ps13(args.db))
