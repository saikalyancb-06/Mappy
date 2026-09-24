"""Database engine, schema bootstrap and capability detection.

SQLite is the zero-setup development store. PostgreSQL + PostGIS + pgvector is
the production target: when available, spatial filtering runs in PostGIS and
vector similarity in pgvector. Capabilities are detected, never assumed.
"""
from __future__ import annotations

import logging
import warnings
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.db.models import SCHEMA_VERSION, Base, Meta

logger = logging.getLogger(__name__)

_is_sqlite = DATABASE_URL.startswith("sqlite")
if _is_sqlite:
    db_path = DATABASE_URL.split("///", 1)[-1]
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


CAPABILITIES: dict[str, object] = {"dialect": engine.dialect.name, "postgis": False, "pgvector": False}

# Tables that hold user-owned data and must survive a schema upgrade.
_USER_TABLES = {"users", "user_preferences", "interaction_events", "feedback", "feedback_vibes", "feedback_aspects", "vibes", "aspects", "user_vibe_preferences", "user_aspect_preferences"}  # never dropped on upgrade
_LEGACY_TABLES = {"areas", "pois", "knowledge_documents", "hotels", "weather_daily", "weather_hourly", "events_festivals", "safety_advisories", "ingestion_jobs", "chat_sessions", "chat_messages", "trips", "itineraries", "itinerary_items", "languages", "currencies"}


def _upgrade_legacy_schema() -> None:
    """Drop derived/cached tables written by an older schema version.

    Only reference data that can be re-imported from packs or providers is
    dropped; user accounts and preferences are preserved and migrated in place.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    current = None
    if "geoguide_meta" in tables:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT value FROM geoguide_meta WHERE key = 'schema_version'")).first()
            current = row[0] if row else None
    if current == SCHEMA_VERSION or not tables:
        return
    # Only GeoGuide's own (current or historical) tables; never extension/foreign tables.
    owned = set(Base.metadata.tables) | _LEGACY_TABLES
    legacy = (tables & owned) - _USER_TABLES
    with engine.begin() as connection:
        for table in legacy:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}"' + (" CASCADE" if not _is_sqlite else "")))
    logger.info("schema_upgrade dropped_tables=%s", sorted(legacy))


def _add_missing_columns() -> None:
    warnings.filterwarnings("ignore", message="Did not recognize type", category=SAWarning)
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or column.primary_key:
                continue
            column_type = column.type.compile(dialect=engine.dialect)
            default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
            clause = f" DEFAULT '{default}'" if isinstance(default, str) else (f" DEFAULT {default}" if isinstance(default, (int, float)) and not isinstance(default, bool) else "")
            with engine.begin() as connection:
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}{clause}'))


def _postgres_extensions() -> None:
    for extension, capability in (("postgis", "postgis"), ("vector", "pgvector")):
        try:
            with engine.begin() as connection:
                connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
            CAPABILITIES[capability] = True
        except Exception as exc:  # extension not installed on the server
            CAPABILITIES[capability] = False
            logger.warning("postgres_extension_unavailable extension=%s error=%s", extension, type(exc).__name__)

    if CAPABILITIES["postgis"]:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE pois ADD COLUMN IF NOT EXISTS geom geography(Point, 4326)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_pois_geom ON pois USING GIST (geom)"))
            connection.execute(text(
                """
                CREATE OR REPLACE FUNCTION geoguide_pois_geom() RETURNS trigger AS $$
                BEGIN
                  NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326)::geography;
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
                """
            ))
            connection.execute(text("DROP TRIGGER IF EXISTS trg_pois_geom ON pois"))
            connection.execute(text("CREATE TRIGGER trg_pois_geom BEFORE INSERT OR UPDATE OF lat, lon ON pois FOR EACH ROW EXECUTE FUNCTION geoguide_pois_geom()"))
            connection.execute(text("UPDATE pois SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography WHERE geom IS NULL"))
    if CAPABILITIES["pgvector"]:
        with engine.begin() as connection:
            # Dimension-less column: the model (and so the dimension) is tracked per row.
            connection.execute(text("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector"))


_INDEXES = (
    # City intelligence look-ups; created idempotently on SQLite and Postgres.
    ("ix_pois_destination_category", "pois", "destination_id, category"),
    ("ix_pois_destination_kind", "pois", "destination_id, kind"),
    ("ix_pois_source_external", "pois", "destination_id, source, external_place_id"),
    ("ix_pois_lat_lon", "pois", "lat, lon"),
    ("ix_knowledge_destination_kind", "knowledge_chunks", "destination_id, kind"),
    ("ix_destinations_status", "destinations", "enrichment_status"),
)


def _ensure_indexes() -> None:
    with engine.begin() as connection:
        for name, table, columns in _INDEXES:
            connection.execute(text(f'CREATE INDEX IF NOT EXISTS {name} ON "{table}" ({columns})'))


def init_db() -> None:
    _upgrade_legacy_schema()
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _ensure_indexes()
    if engine.dialect.name == "postgresql":
        _postgres_extensions()
    with SessionLocal() as db:
        meta = db.get(Meta, "schema_version")
        if meta is None:
            db.add(Meta(key="schema_version", value=SCHEMA_VERSION))
        else:
            meta.value = SCHEMA_VERSION
        db.commit()
