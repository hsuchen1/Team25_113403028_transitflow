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
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================


-- Users and Credentials
CREATE TABLE users (
    user_id VARCHAR(20) PRIMARY KEY,
    full_name VARCHAR(100),
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    date_of_birth DATE,
    registered_at TIMESTAMP,
    is_active BOOLEAN,
    deleted_at TIMESTAMP
);

CREATE TABLE user_credentials (
    c_id SERIAL PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash VARCHAR(255) NOT NULL,
    salt VARCHAR(255) NOT NULL,
    secret_question VARCHAR(255),
    secret_answer_hash VARCHAR(255) NOT NULL,
    deleted_at TIMESTAMP
);

-- Stations
CREATE TABLE metro_stations (
    station_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_interchange_metro BOOLEAN,
    is_interchange_national_rail BOOLEAN,
    interchange_national_rail_station_id VARCHAR(20),
    deleted_at TIMESTAMP
);

CREATE TABLE metro_station_lines (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) REFERENCES metro_stations(station_id),
    line VARCHAR(10),
    deleted_at TIMESTAMP
);

CREATE TABLE national_rail_stations (
    station_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN,
    is_interchange_metro BOOLEAN,
    interchange_metro_station_id VARCHAR(20),
    deleted_at TIMESTAMP
);

CREATE TABLE national_rail_station_lines (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(20) REFERENCES national_rail_stations(station_id),
    line VARCHAR(10),
    deleted_at TIMESTAMP
);

-- Schedules
CREATE TABLE metro_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10),
    direction VARCHAR(20),
    origin_station_id VARCHAR(20) REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(20) REFERENCES metro_stations(station_id),
    first_train_time TIME,
    last_train_time TIME,
    base_fare_usd NUMERIC(10,2),
    per_stop_rate_usd NUMERIC(10,2),
    frequency_min INTEGER,
    stops_in_order JSONB,
    travel_time_from_origin_min JSONB,
    operates_on JSONB,
    deleted_at TIMESTAMP
);

CREATE TABLE national_rail_schedules (
    schedule_id VARCHAR(20) PRIMARY KEY,
    line VARCHAR(10),
    service_type VARCHAR(20),
    direction VARCHAR(20),
    origin_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id),
    first_train_time TIME,
    last_train_time TIME,
    frequency_min INTEGER,
    stops_in_order JSONB,
    passed_through_stations JSONB,
    travel_time_from_origin_min JSONB,
    fare_classes JSONB,
    operates_on JSONB,
    deleted_at TIMESTAMP
);

CREATE TABLE national_rail_seat_layouts (
    layout_id VARCHAR(20) PRIMARY KEY,
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id),
    coaches JSONB,
    deleted_at TIMESTAMP
);

-- Bookings, Trips, Payments, Feedback
CREATE TABLE national_rail_bookings (
    booking_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id),
    schedule_id VARCHAR(20) REFERENCES national_rail_schedules(schedule_id),
    origin_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(20) REFERENCES national_rail_stations(station_id),
    travel_date DATE,
    departure_time TIME,
    ticket_type VARCHAR(20),
    fare_class VARCHAR(20),
    coach VARCHAR(5),
    seat_id VARCHAR(10),
    stops_travelled INTEGER,
    amount_usd NUMERIC(10,2),
    status VARCHAR(20),
    booked_at TIMESTAMP,
    travelled_at TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE metro_trips (
    trip_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id),
    schedule_id VARCHAR(20) REFERENCES metro_schedules(schedule_id),
    origin_station_id VARCHAR(20) REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(20) REFERENCES metro_stations(station_id),
    travel_date DATE,
    ticket_type VARCHAR(20),
    day_pass_ref VARCHAR(20),
    stops_travelled INTEGER,
    amount_usd NUMERIC(10,2),
    status VARCHAR(20),
    purchased_at TIMESTAMP,
    travelled_at TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE payments (
    payment_id VARCHAR(20) PRIMARY KEY,
    national_rail_booking_id VARCHAR(20) REFERENCES national_rail_bookings(booking_id),
    metro_trip_id VARCHAR(20) REFERENCES metro_trips(trip_id),
    amount_usd NUMERIC(10,2),
    method VARCHAR(50),
    status VARCHAR(20),
    paid_at TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE feedback (
    feedback_id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(20) REFERENCES users(user_id),
    national_rail_booking_id VARCHAR(20) REFERENCES national_rail_bookings(booking_id),
    metro_trip_id VARCHAR(20) REFERENCES metro_trips(trip_id),
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMP,
    deleted_at TIMESTAMP
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
