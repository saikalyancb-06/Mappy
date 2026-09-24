from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL
from app.db.models import Base

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text('PRAGMA table_info(users)'))}
        if 'password_hash' not in columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR NOT NULL DEFAULT ''"))
        ingestion_columns = {row[1] for row in connection.execute(text('PRAGMA table_info(ingestion_jobs)'))}
        if 'error' not in ingestion_columns:
            connection.execute(text("ALTER TABLE ingestion_jobs ADD COLUMN error TEXT"))
        poi_columns = {row[1] for row in connection.execute(text('PRAGMA table_info(pois)'))}
        if 'opening_hours' not in poi_columns:
            connection.execute(text("ALTER TABLE pois ADD COLUMN opening_hours TEXT"))
