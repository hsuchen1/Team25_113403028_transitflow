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
    -- PK choice: Surrogate SERIAL key for internal row identity; user_id is kept as UNIQUE NOT NULL
    -- to preserve the natural identifier used across all FK references and application logic.
    -- SERIAL was chosen over UUID v7 for simplicity and lower storage overhead (4 vs 16 bytes).
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(20) UNIQUE NOT NULL,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    -- year_of_birth: stored as SMALLINT because the registration form only collects year;
    -- storing a full DATE would require fabricating month/day, which is semantically incorrect.
    year_of_birth SMALLINT,
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
-- PK choice (applies to all tables below unless noted): every table gets a
-- surrogate `id SERIAL PRIMARY KEY` for internal row identity. The original
-- VARCHAR business identifier (station_id, schedule_id, booking_id, etc.) is
-- kept as UNIQUE NOT NULL — it remains the key used by FK references and
-- application logic (queries.py), so no FK or query changes are needed.
-- Why: per course guidance, VARCHAR primary keys should generally be avoided
-- (no DB-enforced uniqueness until insert time, plus indexing/performance
-- downsides); SERIAL/UUID surrogate keys are the norm, mixed per table need.
-- SERIAL (not UUID) was chosen throughout for consistency with the existing
-- `users`/`user_credentials` pattern and lower storage overhead.
CREATE TABLE metro_stations (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    schedule_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    schedule_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    layout_id VARCHAR(20) UNIQUE NOT NULL,
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    coaches JSONB,
    deleted_at TIMESTAMPTZ
);

-- Bookings, Trips, Payments, Feedback
CREATE TABLE national_rail_bookings (
    id SERIAL PRIMARY KEY,
    booking_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    trip_id VARCHAR(20) UNIQUE NOT NULL,
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
    id SERIAL PRIMARY KEY,
    payment_id VARCHAR(20) UNIQUE NOT NULL,
    -- Polymorphic target: exactly one of the two FKs below is populated (see CHECK).
    -- UNIQUE on each FK enforces at most one payment per booking/trip at the DB
    -- level (previously only guaranteed by the execute_booking write path).
    -- PostgreSQL UNIQUE ignores NULLs, so the many NULL rows on each side are fine.
    national_rail_booking_id VARCHAR(20) UNIQUE REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) UNIQUE REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    amount_usd NUMERIC(10,2),
    method VARCHAR(50),
    status VARCHAR(20),
    paid_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    -- Mutual exclusivity: a payment pays for a rail booking XOR a metro trip,
    -- never both and never neither. Combined with ON DELETE SET NULL this also
    -- means a hard DELETE of a referenced booking/trip is blocked (the cascaded
    -- SET NULL would violate this CHECK and abort) — intended, since financial
    -- records must never be orphaned and the business rule mandates soft delete.
    CHECK (num_nonnulls(national_rail_booking_id, metro_trip_id) = 1)
);

CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    feedback_id VARCHAR(20) UNIQUE NOT NULL,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    national_rail_booking_id VARCHAR(20) REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    -- Same XOR rule as payments: feedback targets a rail booking or a metro trip,
    -- never both/neither. No UNIQUE on the FKs here — unlike payments, a booking
    -- may legitimately receive multiple feedback entries (0..N in the ERD).
    CHECK (num_nonnulls(national_rail_booking_id, metro_trip_id) = 1)
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
