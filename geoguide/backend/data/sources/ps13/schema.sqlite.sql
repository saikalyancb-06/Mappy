-- KV Hackathon 2026 · travel data model v1.1.0-rc1
-- Only the 19 tables this problem statement needs.

-- SQLite has no DECIMAL type, and NUMERIC affinity would turn '8500.00' into the
-- float 8500.0. Money columns are therefore TEXT so the exact value survives.
PRAGMA foreign_keys = ON;

-- categories  (Reference & geography)
CREATE TABLE categories (
  category_id                  TEXT PRIMARY KEY,
  code                         TEXT NOT NULL UNIQUE,
  label                        TEXT NOT NULL,
  parent_category_id           TEXT,
  applies_to                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (parent_category_id) REFERENCES categories(category_id)
);

-- currencies  (Reference & geography)
CREATE TABLE currencies (
  currency_id                  TEXT PRIMARY KEY,
  iso4217                      TEXT NOT NULL UNIQUE,
  name                         TEXT NOT NULL,
  symbol                       TEXT NOT NULL,
  minor_unit_exponent          INTEGER NOT NULL,
  display_locale               TEXT NOT NULL,
  updated_at                   TEXT NOT NULL
);

-- languages  (Reference & geography)
CREATE TABLE languages (
  language_id                  TEXT PRIMARY KEY,
  bcp47                        TEXT NOT NULL UNIQUE,
  english_name                 TEXT NOT NULL,
  native_name                  TEXT NOT NULL,
  script                       TEXT NOT NULL,
  rtl                          INTEGER NOT NULL,
  tts_supported                INTEGER NOT NULL,
  updated_at                   TEXT NOT NULL
);

-- countries  (Reference & geography)
CREATE TABLE countries (
  country_id                   TEXT PRIMARY KEY,
  iso2                         TEXT NOT NULL UNIQUE,
  iso3                         TEXT NOT NULL UNIQUE,
  name                         TEXT NOT NULL,
  default_currency             TEXT NOT NULL,
  calling_code                 TEXT NOT NULL,
  region                       TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (default_currency) REFERENCES currencies(iso4217)
);

-- cities  (Reference & geography)
CREATE TABLE cities (
  city_id                      TEXT PRIMARY KEY,
  name                         TEXT NOT NULL,
  state                        TEXT,
  country_id                   TEXT NOT NULL,
  country_code                 TEXT NOT NULL,
  lat                          NUMERIC(9,6) NOT NULL,
  lng                          NUMERIC(9,6) NOT NULL,
  timezone                     TEXT NOT NULL,
  region                       TEXT NOT NULL,
  population                   INTEGER,
  season_profile               TEXT NOT NULL,
  peak_months                  TEXT NOT NULL,
  primary_language             TEXT NOT NULL,
  description                  TEXT,
  status                       TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (country_id) REFERENCES countries(country_id),
  FOREIGN KEY (primary_language) REFERENCES languages(bcp47)
);

-- events_festivals  (Supply & catalogue)
CREATE TABLE events_festivals (
  event_id                     TEXT PRIMARY KEY,
  city_id                      TEXT NOT NULL,
  name                         TEXT NOT NULL,
  category_id                  TEXT NOT NULL,
  start_date                   TEXT NOT NULL,
  end_date                     TEXT NOT NULL,
  season                       TEXT NOT NULL,
  recurrence                   TEXT NOT NULL,
  expected_footfall            INTEGER,
  is_ticketed                  INTEGER NOT NULL,
  ticket_price                 TEXT,
  currency                     TEXT,
  venue_lat                    NUMERIC(9,6),
  venue_lng                    NUMERIC(9,6),
  description                  TEXT NOT NULL,
  status                       TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  FOREIGN KEY (category_id) REFERENCES categories(category_id),
  FOREIGN KEY (currency) REFERENCES currencies(iso4217)
);

-- hotels  (Supply & catalogue)
CREATE TABLE hotels (
  hotel_id                     TEXT PRIMARY KEY,
  city_id                      TEXT NOT NULL,
  name                         TEXT NOT NULL,
  property_type                TEXT NOT NULL,
  star_rating                  INTEGER NOT NULL,
  guest_score                  NUMERIC(2,1),
  review_count                 INTEGER NOT NULL,
  address_line                 TEXT NOT NULL,
  lat                          NUMERIC(9,6) NOT NULL,
  lng                          NUMERIC(9,6) NOT NULL,
  distance_to_centre_km        NUMERIC(6,2) NOT NULL,
  description                  TEXT NOT NULL,
  base_currency                TEXT NOT NULL,
  checkin_time                 TEXT NOT NULL,
  checkout_time                TEXT NOT NULL,
  chain_code                   TEXT,
  has_xr_scene                 INTEGER NOT NULL,
  status                       TEXT NOT NULL,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  FOREIGN KEY (base_currency) REFERENCES currencies(iso4217)
);

-- safety_advisories  (Content, knowledge & safety)
CREATE TABLE safety_advisories (
  advisory_id                  TEXT PRIMARY KEY,
  city_id                      TEXT NOT NULL,
  advisory_type                TEXT NOT NULL,
  level                        TEXT NOT NULL,
  title                        TEXT NOT NULL,
  body                         TEXT NOT NULL,
  language                     TEXT NOT NULL,
  valid_from                   TEXT NOT NULL,
  valid_to                     TEXT NOT NULL,
  issuing_body                 TEXT NOT NULL,
  affected_area                TEXT,
  source_url                   TEXT,
  status                       TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  FOREIGN KEY (language) REFERENCES languages(bcp47)
);

-- users  (Identity & preference)
CREATE TABLE users (
  user_id                      TEXT PRIMARY KEY,
  display_name                 TEXT NOT NULL,
  email                        TEXT NOT NULL UNIQUE,
  home_city_id                 TEXT NOT NULL,
  home_currency                TEXT NOT NULL,
  locale                       TEXT NOT NULL,
  budget_band                  TEXT NOT NULL,
  travel_style                 TEXT NOT NULL,
  traveller_type               TEXT NOT NULL,
  segment                      TEXT NOT NULL,
  date_of_signup               TEXT NOT NULL,
  loyalty_tier                 TEXT,
  status                       TEXT NOT NULL,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (home_city_id) REFERENCES cities(city_id),
  FOREIGN KEY (home_currency) REFERENCES currencies(iso4217),
  FOREIGN KEY (locale) REFERENCES languages(bcp47)
);

-- weather_daily  (Reference & geography)
CREATE TABLE weather_daily (
  weather_id                   TEXT PRIMARY KEY,
  city_id                      TEXT NOT NULL,
  for_date                     TEXT NOT NULL,
  temp_min_c                   NUMERIC(4,1) NOT NULL,
  temp_max_c                   NUMERIC(4,1) NOT NULL,
  feels_like_c                 NUMERIC(4,1),
  precipitation_mm             NUMERIC(6,2) NOT NULL,
  humidity_pct                 INTEGER NOT NULL,
  wind_kph                     NUMERIC(5,1) NOT NULL,
  condition                    TEXT NOT NULL,
  season                       TEXT NOT NULL,
  is_extreme                   INTEGER NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  UNIQUE (city_id, for_date)
);

-- activities_poi  (Supply & catalogue)
CREATE TABLE activities_poi (
  poi_id                       TEXT PRIMARY KEY,
  city_id                      TEXT NOT NULL,
  name                         TEXT NOT NULL,
  category_id                  TEXT NOT NULL,
  poi_category                 TEXT NOT NULL,
  lat                          NUMERIC(9,6) NOT NULL,
  lng                          NUMERIC(9,6) NOT NULL,
  typical_duration_minutes     INTEGER NOT NULL,
  entry_cost                   TEXT NOT NULL,
  currency                     TEXT NOT NULL,
  carbon_kg                    NUMERIC(8,3) NOT NULL,
  popularity_score             INTEGER NOT NULL,
  value_score                  INTEGER NOT NULL,
  opens_at                     TEXT,
  closes_at                    TEXT,
  closed_days                  TEXT,
  best_season                  TEXT,
  accessibility                TEXT NOT NULL,
  tags                         TEXT NOT NULL,
  description                  TEXT NOT NULL,
  has_xr_scene                 INTEGER NOT NULL,
  status                       TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  FOREIGN KEY (category_id) REFERENCES categories(category_id),
  FOREIGN KEY (currency) REFERENCES currencies(iso4217)
);

-- place_kb  (Content, knowledge & safety)
CREATE TABLE place_kb (
  chunk_id                     TEXT PRIMARY KEY,
  city_id                      TEXT,
  poi_id                       TEXT,
  section                      TEXT NOT NULL,
  title                        TEXT NOT NULL,
  body                         TEXT NOT NULL,
  language                     TEXT NOT NULL,
  token_estimate               INTEGER NOT NULL,
  source_label                 TEXT NOT NULL,
  embedding_ref                TEXT,
  seasonal_relevance           TEXT,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (city_id) REFERENCES cities(city_id),
  FOREIGN KEY (poi_id) REFERENCES activities_poi(poi_id),
  FOREIGN KEY (language) REFERENCES languages(bcp47)
);

-- poi_facts_kb  (Content, knowledge & safety)
CREATE TABLE poi_facts_kb (
  fact_id                      TEXT PRIMARY KEY,
  poi_id                       TEXT NOT NULL,
  fact_type                    TEXT NOT NULL,
  fact_text                    TEXT NOT NULL,
  language                     TEXT NOT NULL,
  confidence                   TEXT NOT NULL,
  display_priority             INTEGER NOT NULL,
  embedding_ref                TEXT,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (poi_id) REFERENCES activities_poi(poi_id),
  FOREIGN KEY (language) REFERENCES languages(bcp47)
);

-- poi_media  (Supply & catalogue)
CREATE TABLE poi_media (
  media_id                     TEXT PRIMARY KEY,
  poi_id                       TEXT NOT NULL,
  file_path                    TEXT NOT NULL,
  media_role                   TEXT NOT NULL,
  alt_text                     TEXT NOT NULL,
  label_class                  TEXT NOT NULL,
  dataset_split                TEXT NOT NULL,
  width_px                     INTEGER NOT NULL,
  height_px                    INTEGER NOT NULL,
  capture_conditions           TEXT,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (poi_id) REFERENCES activities_poi(poi_id)
);

-- trips  (Trip & itinerary)
CREATE TABLE trips (
  trip_id                      TEXT PRIMARY KEY,
  owner_user_id                TEXT NOT NULL,
  title                        TEXT NOT NULL,
  origin_city_id               TEXT,
  destination_city_id          TEXT NOT NULL,
  start_date                   TEXT NOT NULL,
  end_date                     TEXT NOT NULL,
  party_size                   INTEGER NOT NULL,
  adults                       INTEGER NOT NULL,
  children                     INTEGER NOT NULL,
  trip_type                    TEXT NOT NULL,
  is_group_trip                INTEGER NOT NULL,
  status                       TEXT NOT NULL,
  home_currency                TEXT NOT NULL,
  notes                        TEXT,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (owner_user_id) REFERENCES users(user_id),
  FOREIGN KEY (origin_city_id) REFERENCES cities(city_id),
  FOREIGN KEY (destination_city_id) REFERENCES cities(city_id),
  FOREIGN KEY (home_currency) REFERENCES currencies(iso4217)
);

-- user_devices  (Identity & preference)
CREATE TABLE user_devices (
  device_id                    TEXT PRIMARY KEY,
  user_id                      TEXT NOT NULL,
  platform                     TEXT NOT NULL,
  model                        TEXT NOT NULL,
  os_version                   TEXT NOT NULL,
  push_token                   TEXT,
  locale                       TEXT NOT NULL,
  last_lat                     NUMERIC(9,6),
  last_lng                     NUMERIC(9,6),
  last_seen_at                 TEXT,
  device_class                 TEXT NOT NULL,
  xr_capability                TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(user_id),
  FOREIGN KEY (locale) REFERENCES languages(bcp47)
);

-- user_preferences  (Identity & preference)
CREATE TABLE user_preferences (
  preference_id                TEXT PRIMARY KEY,
  user_id                      TEXT NOT NULL UNIQUE,
  preferred_languages          TEXT NOT NULL,
  guide_language               TEXT,
  interests                    TEXT NOT NULL,
  dietary_flags                TEXT,
  accessibility_needs          TEXT,
  preferred_currency           TEXT NOT NULL,
  max_daily_budget             TEXT,
  max_daily_budget_currency    TEXT,
  pace                         TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(user_id),
  FOREIGN KEY (guide_language) REFERENCES languages(bcp47),
  FOREIGN KEY (preferred_currency) REFERENCES currencies(iso4217),
  FOREIGN KEY (max_daily_budget_currency) REFERENCES currencies(iso4217)
);

-- itineraries  (Trip & itinerary)
CREATE TABLE itineraries (
  itinerary_id                 TEXT PRIMARY KEY,
  trip_id                      TEXT NOT NULL,
  name                         TEXT NOT NULL,
  version                      INTEGER NOT NULL,
  is_active                    INTEGER NOT NULL,
  generated_by                 TEXT NOT NULL,
  total_cost                   TEXT NOT NULL,
  currency                     TEXT NOT NULL,
  total_duration_minutes       INTEGER NOT NULL,
  total_carbon_kg              NUMERIC(10,3) NOT NULL,
  optimizer_weights            TEXT,
  status                       TEXT NOT NULL,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (trip_id) REFERENCES trips(trip_id),
  FOREIGN KEY (currency) REFERENCES currencies(iso4217)
);

-- itinerary_items  (Trip & itinerary)
CREATE TABLE itinerary_items (
  item_id                      TEXT PRIMARY KEY,
  itinerary_id                 TEXT NOT NULL,
  day_index                    INTEGER NOT NULL,
  sort_order                   INTEGER NOT NULL,
  starts_at                    TEXT,
  ends_at                      TEXT,
  item_type                    TEXT NOT NULL,
  entity_type                  TEXT,
  entity_id                    TEXT,
  title                        TEXT NOT NULL,
  cost                         TEXT NOT NULL,
  currency                     TEXT NOT NULL,
  carbon_kg                    NUMERIC(8,3) NOT NULL,
  duration_minutes             INTEGER NOT NULL,
  source                       TEXT NOT NULL,
  explanation                  TEXT,
  locked                       INTEGER NOT NULL,
  status                       TEXT NOT NULL,
  created_at                   TEXT NOT NULL,
  updated_at                   TEXT NOT NULL,
  FOREIGN KEY (itinerary_id) REFERENCES itineraries(itinerary_id),
  FOREIGN KEY (currency) REFERENCES currencies(iso4217)
);
