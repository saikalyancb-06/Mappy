from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Area(Base):
    __tablename__ = 'areas'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    country = Column(String, nullable=True)
    region = Column(String, nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    timezone = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Poi(Base):
    __tablename__ = 'pois'

    id = Column(String, primary_key=True)
    area_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    rating = Column(Float, nullable=True)
    open_now = Column(Boolean, default=False)
    price_level = Column(String, nullable=True)
    source = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Hotel(Base):
    __tablename__ = 'hotels'

    id = Column(String, primary_key=True)
    area_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    price_level = Column(String, nullable=True)
    open_now = Column(Boolean, default=True)
    source = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class WeatherDaily(Base):
    __tablename__ = 'weather_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    area_id = Column(String, nullable=False, index=True)
    date = Column(String, nullable=False)
    temp_min = Column(Float, nullable=True)
    temp_max = Column(Float, nullable=True)
    summary = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class WeatherHourly(Base):
    __tablename__ = 'weather_hourly'

    id = Column(Integer, primary_key=True, autoincrement=True)
    area_id = Column(String, nullable=False, index=True)
    hour = Column(String, nullable=False)
    temperature = Column(Float, nullable=True)
    precipitation = Column(Float, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class EventFestival(Base):
    __tablename__ = 'events_festivals'

    id = Column(String, primary_key=True)
    area_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    event_date = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class SafetyAdvisory(Base):
    __tablename__ = 'safety_advisories'

    id = Column(String, primary_key=True)
    area_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    severity = Column(String, nullable=True)
    valid_from = Column(String, nullable=True)
    valid_to = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = 'users'

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserPreference(Base):
    __tablename__ = 'user_preferences'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    language = Column(String, default='en')
    budget = Column(String, default='moderate')
    pace = Column(String, default='balanced')
    interests = Column(Text, default='{}')
    likes = Column(Text, default='[]')
    dislikes = Column(Text, default='[]')
    updated_at = Column(DateTime, default=datetime.utcnow)


class InteractionEvent(Base):
    __tablename__ = 'interaction_events'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    poi_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class IngestionJob(Base):
    __tablename__ = 'ingestion_jobs'

    id = Column(String, primary_key=True)
    area_id = Column(String, nullable=False, index=True)
    status = Column(String, default='queued')
    step = Column(String, default='pending')
    progress = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ChatSession(Base):
    __tablename__ = 'chat_sessions'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = 'chat_messages'

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Trip(Base):
    __tablename__ = 'trips'

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    start_time = Column(String, nullable=True)
    duration_hours = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Itinerary(Base):
    __tablename__ = 'itineraries'

    id = Column(String, primary_key=True)
    trip_id = Column(String, nullable=False, index=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ItineraryItem(Base):
    __tablename__ = 'itinerary_items'

    id = Column(String, primary_key=True)
    itinerary_id = Column(String, nullable=False, index=True)
    poi_id = Column(String, nullable=False)
    position = Column(Integer, default=0)
    start_time = Column(String, nullable=True)
    end_time = Column(String, nullable=True)


class Language(Base):
    __tablename__ = 'languages'

    code = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)


class Currency(Base):
    __tablename__ = 'currencies'

    code = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    symbol = Column(String, nullable=True)
