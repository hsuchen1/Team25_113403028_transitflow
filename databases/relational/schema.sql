-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
-- ============================================================

-- Users and Credentials
CREATE TABLE users (
    -- PK choice: We use Natural Keys (VARCHAR) derived from the dataset for primary entities to simplify data seeding and lookup.
    user_id VARCHAR(20) PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE,
    registered_at TIMESTAMPTZ,
    is_active BOOLEAN,
    -- Delete strategy: We use Soft Delete (deleted_at TIMESTAMPTZ) to preserve historical integrity, particularly for bookings and financial records, complying with standard business rules.
    deleted_at TIMESTAMPTZ
);

CREATE TABLE user_credentials (
    -- PK choice: Surrogate SERIAL key used for credentials table as it represents an internal system record mapped to a user.
    c_id SERIAL PRIMARY KEY,
    user_id VARCHAR(20) UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash VARCHAR(255) NOT NULL,
    secret_question VARCHAR(255),
    secret_answer_hash VARCHAR(255) NOT NULL,
    deleted_at TIMESTAMPTZ
);

-- Stations
CREATE TABLE metro_stations (
    station_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_interchange_metro BOOLEAN,
    is_interchange_national_rail BOOLEAN,
    interchange_national_rail_station_id VARCHAR(20),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE metro_station_lines (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    line VARCHAR(10),
    deleted_at TIMESTAMPTZ,
    UNIQUE(station_id, line)
);

CREATE TABLE national_rail_stations (
    station_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN,
    is_interchange_metro BOOLEAN,
    interchange_metro_station_id VARCHAR(20),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE national_rail_station_lines (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    line VARCHAR(10),
    deleted_at TIMESTAMPTZ,
    UNIQUE(station_id, line)
);

-- Schedules
CREATE TABLE metro_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10),
    direction VARCHAR(20),
    origin_station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    first_train_time TIME,
    last_train_time TIME,
    base_fare_usd NUMERIC(10,2),
    per_stop_rate_usd NUMERIC(10,2),
    frequency_min INTEGER,
    operates_on JSONB,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE metro_schedule_stops (
    id SERIAL PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stop_order INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    deleted_at TIMESTAMPTZ,
    UNIQUE(schedule_id, station_id)
);

CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10),
    service_type VARCHAR(20),
    direction VARCHAR(20),
    origin_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    first_train_time TIME,
    last_train_time TIME,
    frequency_min INTEGER,
    passed_through_stations JSONB,
    fare_classes JSONB,
    operates_on JSONB,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE national_rail_schedule_stops (
    id SERIAL PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    stop_order INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    deleted_at TIMESTAMPTZ,
    UNIQUE(schedule_id, station_id)
);

CREATE TABLE national_rail_seat_layouts (
    layout_id VARCHAR(20) PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    coaches JSONB,
    deleted_at TIMESTAMPTZ
);

-- Bookings, Trips, Payments, Feedback
CREATE TABLE national_rail_bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date DATE,
    departure_time TIME,
    ticket_type VARCHAR(20),
    fare_class VARCHAR(20),
    coach VARCHAR(5),
    seat_id VARCHAR(10),
    stops_travelled INTEGER,
    amount_usd NUMERIC(10,2),
    status VARCHAR(20),
    booked_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE metro_trips (
    trip_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    schedule_id VARCHAR(20) REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(20) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date DATE,
    ticket_type VARCHAR(20),
    day_pass_ref VARCHAR(20),
    stops_travelled INTEGER,
    amount_usd NUMERIC(10,2),
    status VARCHAR(20),
    purchased_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE payments (
    payment_id VARCHAR(20) PRIMARY KEY,
    national_rail_booking_id VARCHAR(20) REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    amount_usd NUMERIC(10,2),
    method VARCHAR(50),
    status VARCHAR(20),
    paid_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

CREATE TABLE feedback (
    feedback_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    national_rail_booking_id VARCHAR(20) REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ
);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_documents_embedding_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);
