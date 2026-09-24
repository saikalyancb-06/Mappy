"""Relational system of record.

Structured facts (coordinates, categories, fees, hours, ratings) live in
relational columns. Semantic text lives in ``knowledge_chunks`` with its
embedding metadata. Every data row carries provenance (source, source_url,
confidence, timestamps).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

SCHEMA_VERSION = "2"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Meta(Base):
    __tablename__ = "geoguide_meta"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)


class DataSource(Base):
    """Provenance record for an imported dataset (pack)."""

    __tablename__ = "data_sources"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    license = Column(String, nullable=True)
    url = Column(String, nullable=True)
    collected_at = Column(String, nullable=True)
    geographic_coverage = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    version = Column(String, nullable=True)
    imported_at = Column(DateTime, default=utcnow)


class Destination(Base):
    __tablename__ = "destinations"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    region = Column(String, nullable=True)
    country = Column(String, nullable=True)
    country_code = Column(String, nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    coverage_radius_km = Column(Float, nullable=False, default=10.0)
    timezone = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    languages = Column(Text, default="[]")
    summary = Column(Text, nullable=True)
    population = Column(Integer, nullable=True)
    peak_months = Column(Text, nullable=True)  # JSON list of month numbers
    season_profile = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    data_source_id = Column(String, nullable=True, index=True)
    curated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "normalized", name="uq_entity_alias"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_type = Column(String, nullable=False)  # destination | poi
    entity_id = Column(String, nullable=False, index=True)
    alias = Column(String, nullable=False)
    normalized = Column(String, nullable=False, index=True)


class Poi(Base):
    """Any point of interest: attractions, activities, food, stays, services."""

    __tablename__ = "pois"

    id = Column(String, primary_key=True)
    destination_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False, default="attraction")  # attraction | activity | food | stay | service
    category = Column(String, nullable=True, index=True)
    tags = Column(Text, default="[]")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    coordinate_precision = Column(String, nullable=True)  # exact | approximate
    address = Column(Text, nullable=True)
    neighborhood = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    opening_hours = Column(Text, nullable=True)  # structured JSON (see app/geo/opening_hours.py)
    opening_hours_raw = Column(Text, nullable=True)  # provider string, e.g. OSM syntax
    entry_fee = Column(Float, nullable=True)  # for sorting/filtering only; display uses entry_cost
    entry_cost = Column(String, nullable=True)  # exact 2-place decimal text, paired with fee_currency (never a float)
    entry_fee_foreign = Column(Float, nullable=True)
    fee_currency = Column(String, nullable=True)
    fee_notes = Column(Text, nullable=True)
    price_level = Column(Integer, nullable=True)  # 0 free .. 4 luxury
    visit_duration_min = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)  # 0-5 scale
    review_count = Column(Integer, nullable=True)
    popularity_score = Column(Integer, nullable=True)  # 0-100 (dataset)
    value_score = Column(Integer, nullable=True)  # 0-100 (dataset)
    carbon_kg = Column(Float, nullable=True)  # footprint of a visit (dataset)
    star_rating = Column(Integer, nullable=True)  # stays
    guest_score = Column(Float, nullable=True)  # stays, 0-10 as published
    property_type = Column(String, nullable=True)  # stays
    checkin_time = Column(String, nullable=True)
    checkout_time = Column(String, nullable=True)
    distance_to_centre_km = Column(Float, nullable=True)
    status = Column(String, nullable=True, default="active")
    step_free = Column(Boolean, nullable=True)
    accessibility_notes = Column(Text, nullable=True)
    indoor = Column(Boolean, nullable=True)
    walking_effort = Column(String, nullable=True)  # low | moderate | high
    best_time = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    website = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    source_id = Column(String, nullable=True)
    data_source_id = Column(String, nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    fetched_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class KnowledgeChunk(Base):
    """Semantic knowledge (place_kb / poi_facts_kb / trusted web evidence)."""

    __tablename__ = "knowledge_chunks"

    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    destination_id = Column(String, nullable=True, index=True)
    poi_id = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False, default="place_kb")  # place_kb | poi_fact | web
    category = Column(String, nullable=True)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    language = Column(String, default="en")
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    data_source_id = Column(String, nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    embedding_model = Column(String, nullable=True)
    embedding_dim = Column(Integer, nullable=True)
    embedding = Column(Text, nullable=True)  # JSON float list (portable); pgvector column mirrors it on Postgres


class SafetyAdvisory(Base):
    __tablename__ = "safety_advisories"

    id = Column(String, primary_key=True)
    destination_id = Column(String, nullable=True, index=True)
    poi_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    category = Column(String, nullable=True)  # heat | water | wildlife | crowd | transport | health | general
    severity = Column(String, nullable=False, default="info")  # stored as issued: info | low | advisory | caution | moderate | warning | high | severe
    language = Column(String, nullable=True)
    issuing_body = Column(String, nullable=True)
    affected_area = Column(String, nullable=True)
    active_months = Column(Text, nullable=True)  # JSON list of month numbers; null = all year
    valid_from = Column(String, nullable=True)
    valid_to = Column(String, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    data_source_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class EventFestival(Base):
    __tablename__ = "events_festivals"

    id = Column(String, primary_key=True)
    destination_id = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    typical_months = Column(Text, nullable=True)  # JSON list when exact dates vary by year
    recurrence = Column(String, nullable=True)
    is_ticketed = Column(Boolean, nullable=True)
    ticket_price = Column(String, nullable=True)  # decimal text
    currency = Column(String, nullable=True)
    venue_lat = Column(Float, nullable=True)
    venue_lon = Column(Float, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    data_source_id = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=utcnow)
    # City-and-date event model (all additive). start_date/end_date are ISO dates; a record
    # without them is only "associated with" the city and is never shown as happening on a date.
    event_type = Column(String, nullable=True)  # festival (recurring/cultural) | live (concert, exhibition, match…)
    category = Column(String, nullable=True)  # festival | music | culture | arts | sports | food | family | markets | outdoors | nightlife | other
    city_key = Column(String, nullable=True, index=True)  # normalised "city|country" for events in cities without a stored destination
    venue_name = Column(String, nullable=True)
    significance = Column(Text, nullable=True)
    traditions = Column(Text, nullable=True)
    etiquette = Column(Text, nullable=True)
    season = Column(String, nullable=True)
    expected_footfall = Column(Integer, nullable=True)
    status = Column(String, nullable=True)  # active | cancelled | postponed
    source_id = Column(String, nullable=True)  # the provider's own id, used to de-duplicate live listings
    source_published_at = Column(String, nullable=True)
    last_verified_at = Column(String, nullable=True)  # when a source last confirmed this record (ISO)
    start_time = Column(String, nullable=True)  # "HH:MM" local, when the source states it
    end_time = Column(String, nullable=True)
    price_kind = Column(String, nullable=True)  # free | paid | donation | unknown, when the source states it


class EventSubmission(Base):
    """An event sent in by an organiser, venue or attendee. Nothing is shown until a moderator approves it."""

    __tablename__ = "event_submissions"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # organiser | venue | attendee
    organiser_name = Column(String, nullable=False)
    contact_email = Column(String, nullable=False)  # private: moderators only, never published
    destination_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=False)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    start_time = Column(String, nullable=True)
    end_time = Column(String, nullable=True)
    venue_name = Column(String, nullable=False)
    venue_address = Column(String, nullable=True)
    venue_lat = Column(Float, nullable=True)
    venue_lon = Column(Float, nullable=True)
    price_kind = Column(String, nullable=False, default="unknown")  # free | paid | donation | unknown
    price_min = Column(String, nullable=True)  # decimal text
    currency = Column(String, nullable=True)
    ticket_url = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)  # pending | approved | rejected
    review_note = Column(Text, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    event_id = Column(String, nullable=True)  # the published EventFestival row
    created_at = Column(DateTime, default=utcnow)


class WeatherDaily(Base):
    """Daily weather from a dataset (e.g. the organiser pack). Used only when live weather is unavailable, and labelled so."""

    __tablename__ = "weather_daily"
    __table_args__ = (UniqueConstraint("destination_id", "for_date", name="uq_weather_daily"),)

    id = Column(String, primary_key=True)
    destination_id = Column(String, nullable=False, index=True)
    for_date = Column(String, nullable=False)
    temp_min_c = Column(Float, nullable=True)
    temp_max_c = Column(Float, nullable=True)
    feels_like_c = Column(Float, nullable=True)
    precipitation_mm = Column(Float, nullable=True)
    humidity_pct = Column(Integer, nullable=True)
    wind_kph = Column(Float, nullable=True)
    condition = Column(String, nullable=True)
    is_extreme = Column(Boolean, nullable=True)
    data_source_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    key = Column(String, primary_key=True)
    namespace = Column(String, nullable=False, index=True)
    data = Column(Text, nullable=False)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=True)
    password_hash = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=utcnow)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    language = Column(String, default="en")
    budget = Column(String, default="moderate")  # low | moderate | high
    max_daily_budget = Column(String, nullable=True)  # decimal text, e.g. "2500.00"
    budget_currency = Column(String, nullable=True)
    pace = Column(String, default="balanced")  # relaxed | balanced | packed
    walking = Column(String, default="moderate")  # low | moderate | high tolerance
    travel_mode = Column(String, nullable=True)  # walk | bicycle | motorbike | car | auto | transit
    accessibility = Column(Text, default="[]")  # e.g. ["step_free"]
    interests = Column(Text, default="{}")
    likes = Column(Text, default="[]")
    dislikes = Column(Text, default="[]")
    updated_at = Column(DateTime, default=utcnow)


class InteractionEvent(Base):
    __tablename__ = "interaction_events"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    poi_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(String, primary_key=True)
    destination_id = Column(String, nullable=True, index=True)
    status = Column(String, default="queued")
    step = Column(String, default="pending")
    progress = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class Trip(Base):
    __tablename__ = "trips"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=True, index=True)
    destination_id = Column(String, nullable=True)
    start_time = Column(String, nullable=True)
    duration_hours = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Itinerary(Base):
    __tablename__ = "itineraries"

    id = Column(String, primary_key=True)
    trip_id = Column(String, nullable=False, index=True)
    preset = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    plan = Column(Text, nullable=True)  # full JSON plan with totals and explanations
    created_at = Column(DateTime, default=utcnow)


class ItineraryItem(Base):
    __tablename__ = "itinerary_items"

    id = Column(String, primary_key=True)
    itinerary_id = Column(String, nullable=False, index=True)
    poi_id = Column(String, nullable=False)
    position = Column(Integer, default=0)
    start_time = Column(String, nullable=True)
    end_time = Column(String, nullable=True)
    locked = Column(Boolean, default=False)


# ---- Feedback & vibe intelligence ------------------------------------------------------------
# Community signals (vibes, crowding, value…) are probabilistic and user-generated; they live here,
# separate from place facts (address, hours, coordinates, category) in the pois table.


class Vibe(Base):
    """Controlled vibe vocabulary (synced from config) plus custom vibes users typed under "Other"."""

    __tablename__ = "vibes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False)
    emoji = Column(String, nullable=True)
    origin = Column(String, nullable=False, default="controlled")  # controlled | custom
    status = Column(String, nullable=False, default="active")  # active | pending (custom, not yet used in ranking) | blocked
    use_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow)


class Aspect(Base):
    """Liked / disliked characteristics ("Good views", "Too crowded"…)."""

    __tablename__ = "aspects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    label = Column(String, nullable=False)
    polarity = Column(String, nullable=False)  # positive | negative
    origin = Column(String, nullable=False, default="controlled")
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime, default=utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=True, index=True)
    place_id = Column(String, nullable=False, index=True)
    place_name = Column(String, nullable=True)
    city = Column(String, nullable=True)
    destination_id = Column(String, nullable=True, index=True)
    category = Column(String, nullable=True)
    visit_date = Column(String, nullable=True)  # ISO date
    overall_rating = Column(Integer, nullable=False)  # 1..5
    recommendation = Column(String, nullable=False)  # loved_it | good | okay | didnt_like | avoid
    crowd_level = Column(String, nullable=True)
    price_level = Column(String, nullable=True)
    ambience_score = Column(Integer, nullable=True)
    cleanliness_score = Column(Integer, nullable=True)
    service_score = Column(Integer, nullable=True)
    accessibility_score = Column(Integer, nullable=True)
    photo_worthiness = Column(Integer, nullable=True)
    text_feedback = Column(Text, nullable=True)  # the traveller's words, stored as written
    sentiment = Column(String, nullable=True)  # positive | neutral | mixed | negative (derived)
    sentiment_score = Column(Float, nullable=True)
    derived = Column(Text, nullable=True)  # JSON: what text analysis found (aspects, vibes, descriptors); never overrides selections
    derived_vibe_scores = Column(Text, nullable=True)  # JSON: vibe → weight this feedback contributes
    is_synthetic = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="user")  # user | synthetic_bootstrap
    created_at = Column(DateTime, default=utcnow, index=True)


class FeedbackVibe(Base):
    __tablename__ = "feedback_vibes"
    __table_args__ = (UniqueConstraint("feedback_id", "vibe_id", name="uq_feedback_vibe"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    feedback_id = Column(String, nullable=False, index=True)
    vibe_id = Column(Integer, nullable=False, index=True)
    origin = Column(String, nullable=False, default="selected")  # selected (authoritative) | derived (from text) | custom
    weight = Column(Float, nullable=False, default=1.0)


class FeedbackAspect(Base):
    __tablename__ = "feedback_aspects"
    __table_args__ = (UniqueConstraint("feedback_id", "aspect_id", name="uq_feedback_aspect"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    feedback_id = Column(String, nullable=False, index=True)
    aspect_id = Column(Integer, nullable=False, index=True)
    origin = Column(String, nullable=False, default="selected")
    weight = Column(Float, nullable=False, default=1.0)


class PlaceVibeProfile(Base):
    """How strongly visitors associate a place with each vibe (0..1), shrunk towards a prior when evidence is thin."""

    __tablename__ = "place_vibe_profiles"
    __table_args__ = (UniqueConstraint("place_id", "vibe_id", name="uq_place_vibe"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(String, nullable=False, index=True)
    vibe_id = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    selections = Column(Float, nullable=False, default=0.0)  # recency/rating-weighted selections
    support = Column(Integer, nullable=False, default=0)  # feedback records that mention this vibe
    confidence = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime, default=utcnow)


class PlaceAspectSignal(Base):
    """Share of (weighted) visitors reporting a characteristic, e.g. too_crowded 0.62 — a general place-level signal."""

    __tablename__ = "place_aspect_signals"
    __table_args__ = (UniqueConstraint("place_id", "aspect_id", name="uq_place_aspect"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    place_id = Column(String, nullable=False, index=True)
    aspect_id = Column(Integer, nullable=False)
    rate = Column(Float, nullable=False)
    support = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=utcnow)


class PlaceFeedbackSummary(Base):
    __tablename__ = "place_feedback_summaries"

    place_id = Column(String, primary_key=True)
    feedback_count = Column(Integer, nullable=False, default=0)
    weighted_count = Column(Float, nullable=False, default=0.0)
    average_rating = Column(Float, nullable=True)
    bayes_rating = Column(Float, nullable=True)  # shrunk towards the global mean when feedback is thin
    recommend_share = Column(Float, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    synthetic_share = Column(Float, nullable=False, default=0.0)
    last_feedback_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class UserVibePreference(Base):
    """A traveller's demonstrated affinity for a vibe: 0.5 neutral, towards 1 liked, towards 0 disliked."""

    __tablename__ = "user_vibe_preferences"
    __table_args__ = (UniqueConstraint("user_id", "vibe_id", name="uq_user_vibe"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    vibe_id = Column(Integer, nullable=False)
    affinity = Column(Float, nullable=False)
    evidence = Column(Float, nullable=False, default=0.0)
    support = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=utcnow)


class UserAspectPreference(Base):
    """A traveller's aversion to a characteristic (too_crowded, too_expensive…): user-specific, never a place penalty."""

    __tablename__ = "user_aspect_preferences"
    __table_args__ = (UniqueConstraint("user_id", "aspect_id", name="uq_user_aspect"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False, index=True)
    aspect_id = Column(Integer, nullable=False)
    aversion = Column(Float, nullable=False)
    support = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=utcnow)
