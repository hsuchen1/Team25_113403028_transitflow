"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to filter by day and count bookings;
                         omit for general info (available_seats will be NULL).

    Returns:
        List of schedule dicts, each containing schedule_id, line, service_type,
        direction, first_train_time, last_train_time, frequency_min, fare_classes,
        stops_travelled, and available_seats.
    """
    from datetime import datetime

    # Convert travel_date to day-of-week string for operates_on filtering
    day_str = None
    if travel_date:
        day_str = datetime.strptime(travel_date, "%Y-%m-%d").strftime("%a").lower()  # e.g. "mon"

    # available_seats subquery: total seats in layout minus confirmed/completed bookings
    # Only meaningful when travel_date is provided; returns NULL otherwise.
    available_seats_expr = """
        (
            SELECT (
                -- Count total seats across all coaches in the layout
                SELECT COALESCE(SUM(jsonb_array_length(coach -> 'seats')), 0)
                FROM national_rail_seat_layouts sl,
                     jsonb_array_elements(sl.coaches) AS coach
                WHERE sl.schedule_id = s.schedule_id
            ) - (
                -- Subtract seats already booked on the requested date
                SELECT COUNT(*)
                FROM national_rail_bookings b
                WHERE b.schedule_id = s.schedule_id
                  AND b.travel_date = %s::date
                  AND b.status != 'cancelled'
            )
        )
    """ if travel_date else "NULL::integer"

    params = [origin_id, destination_id]
    date_filter = ""
    if travel_date:
        # Filter schedules that operate on the requested day of week
        date_filter = f"AND s.operates_on ? '{day_str}'"
        params.insert(0, travel_date)  # for available_seats subquery

    sql = f"""
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            s.fare_classes,
            dest.stop_order - orig.stop_order  AS stops_travelled,
            {available_seats_expr}             AS available_seats
        FROM national_rail_schedules s
        JOIN national_rail_schedule_stops orig ON orig.schedule_id = s.schedule_id
            AND orig.station_id = %s
        JOIN national_rail_schedule_stops dest ON dest.schedule_id = s.schedule_id
            AND dest.station_id = %s
        WHERE orig.stop_order < dest.stop_order
          {date_filter}
        ORDER BY s.line, s.first_train_time
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd.
        None if schedule or fare_class not found.
    """
    sql = """
        SELECT
            fare_classes -> %s -> 'base_fare_usd'    AS base_fare_usd,
            fare_classes -> %s -> 'per_stop_rate_usd' AS per_stop_rate_usd
        FROM national_rail_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (fare_class, fare_class, schedule_id))
            row = cur.fetchone()

    if row is None or row["base_fare_usd"] is None:
        return None

    base     = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    return {
        "fare_class":        fare_class,
        "base_fare_usd":     base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd":    round(base + per_stop * stops_travelled, 2),
    }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"

    Returns:
        List of schedule dicts. Each dict includes stops_in_order (rebuilt from
        metro_schedule_stops via array_agg) so agent.py can compute stop counts
        without a separate query.
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            s.origin_station_id,
            s.destination_station_id,
            -- Rebuild stops_in_order from the junction table so callers can
            -- compute stop counts without an additional query.
            array_agg(ss.station_id ORDER BY ss.stop_order) AS stops_in_order
        FROM metro_schedules s
        JOIN metro_schedule_stops orig ON orig.schedule_id = s.schedule_id
            AND orig.station_id = %s
        JOIN metro_schedule_stops dest ON dest.schedule_id = s.schedule_id
            AND dest.station_id = %s
        JOIN metro_schedule_stops ss   ON ss.schedule_id   = s.schedule_id
        WHERE orig.stop_order < dest.stop_order
        GROUP BY s.schedule_id
        ORDER BY s.line
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with base_fare_usd, per_stop_rate_usd, total_fare_usd.
        None if schedule not found.
    """
    sql = """
        SELECT base_fare_usd, per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

    if row is None:
        return None

    base = float(row["base_fare_usd"])
    per_stop = float(row["per_stop_rate_usd"])
    return {
        "base_fare_usd":     base,
        "per_stop_rate_usd": per_stop,
        "total_fare_usd":    round(base + per_stop * stops_travelled, 2),
    }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    Args:
        schedule_id:  e.g. "NR_SCH01"
        travel_date:  e.g. "2025-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List of dicts: {seat_id, coach, row, column}.
        Empty list if no seats available or schedule has no layout.
    """
    sql = """
        SELECT
            seat ->> 'seat_id' AS seat_id,
            coach ->> 'coach'  AS coach,
            (seat ->> 'row')::integer AS row,
            seat ->> 'column'  AS column
        FROM national_rail_seat_layouts sl,
             jsonb_array_elements(sl.coaches) AS coach,
             jsonb_array_elements(coach -> 'seats') AS seat
        WHERE sl.schedule_id = %s
          AND coach ->> 'fare_class' = %s
          AND seat ->> 'seat_id' NOT IN (
              -- Exclude seats already booked on the requested date
              SELECT seat_id
              FROM national_rail_bookings
              WHERE schedule_id = %s
                AND travel_date = %s::date
                AND status != 'cancelled'
          )
        ORDER BY (seat ->> 'row')::integer, seat ->> 'column'
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, schedule_id, travel_date))
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """
    Return a user's profile by email.

    Args:
        user_email: The user's email address.

    Returns:
        dict with keys user_id, email, first_name, surname, full_name,
        year_of_birth, phone, is_active, registered_at.
        None if not found or account is inactive/deleted.
    """
    sql = """
        SELECT
            user_id,
            email,
            first_name,
            last_name                              AS surname,
            first_name || ' ' || last_name         AS full_name,
            year_of_birth,
            phone,
            is_active,
            registered_at
        FROM users
        WHERE email = %s
          AND is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).

    Args:
        user_email: The user's email address.

    Returns:
        dict with keys 'national_rail' (list) and 'metro' (list).
        Both keys are always present even when one or both lists are empty.
    """
    sql_nr = """
        SELECT b.*
        FROM national_rail_bookings b
        JOIN users u ON u.user_id = b.user_id
        WHERE u.email = %s
        ORDER BY b.booked_at DESC
    """
    sql_metro = """
        SELECT t.*
        FROM metro_trips t
        JOIN users u ON u.user_id = t.user_id
        WHERE u.email = %s
        ORDER BY t.purchased_at DESC
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_nr, (user_email,))
            national_rail = [dict(row) for row in cur.fetchall()]

            cur.execute(sql_metro, (user_email,))
            metro = [dict(row) for row in cur.fetchall()]

    return {"national_rail": national_rail, "metro": metro}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment record for a national rail booking or metro trip.

    Args:
        booking_id: A national rail booking_id (e.g. "BK001") or metro trip_id (e.g. "MT001").

    Returns:
        dict with payment_id, amount_usd, method, status, paid_at.
        None if no payment record found.
    """
    sql = """
        SELECT payment_id, amount_usd, method, status, paid_at
        FROM payments
        WHERE national_rail_booking_id = %s
           OR metro_trip_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id, booking_id))
            row = cur.fetchone()
            return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        ticket_type:            "single" (default) or "return"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type:
      - Normal service: RF001 windows (100% / 75% / 50% / 0%)
      - Express service: RF002 windows (100% / 50% / 0%)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user and store hashed credentials atomically.

    Args:
        email:           The user's email address (must be unique).
        first_name:      The user's first name.
        surname:         The user's last name.
        year_of_birth:   The user's year of birth (integer).
        password:        The plaintext password to hash and store using argon2id.
        secret_question: The security question for account recovery.
        secret_answer:   The plaintext answer; hashed case-insensitively via .lower().

    Returns:
        (True, user_id) on success.
        (False, error_message) on failure (e.g. duplicate email).
    """
    from argon2 import PasswordHasher
    from datetime import datetime, timezone

    ph = PasswordHasher()
    password_hash = ph.hash(password)
    secret_answer_hash = ph.hash(secret_answer.lower())  # case-insensitive: hash lowercased answer
    now = datetime.now(timezone.utc)

    sql_max_id = """
        SELECT MAX(CAST(SUBSTRING(user_id FROM 3) AS INTEGER)) AS max_num
        FROM users
        WHERE user_id LIKE 'RU%'
    """
    sql_insert_user = """
        INSERT INTO users
            (user_id, first_name, last_name, email, year_of_birth, registered_at, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
    """
    sql_insert_cred = """
        INSERT INTO user_credentials
            (user_id, password_hash, secret_question, secret_answer_hash)
        VALUES (%s, %s, %s, %s)
    """

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Derive next user_id from current maximum RU number
            cur.execute(sql_max_id)
            row = cur.fetchone()
            max_num = row["max_num"] if row["max_num"] is not None else 0
            user_id = f"RU{max_num + 1}"

            cur.execute(sql_insert_user, (
                user_id, first_name, surname, email, year_of_birth, now
            ))
            cur.execute(sql_insert_cred, (
                user_id, password_hash, secret_question, secret_answer_hash
            ))

        conn.commit()
        return True, user_id

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False, "Email already registered."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials and return the user's profile on success.

    Args:
        email:    The user's email address.
        password: The plaintext password to verify against the stored argon2 hash.

    Returns:
        dict with keys user_id, email, first_name, surname on success.
        None if email not found, account is inactive, or password is incorrect.
    """
    sql = """
        SELECT
            u.user_id,
            u.email,
            u.first_name,
            u.last_name  AS surname,
            uc.password_hash
        FROM users u
        JOIN user_credentials uc ON uc.user_id = u.user_id
        WHERE u.email = %s
          AND u.is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if row is None:
        return None  # email not found or account inactive

    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    ph = PasswordHasher()
    try:
        ph.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None  # wrong password

    return {
        "user_id":    row["user_id"],
        "email":      row["email"],
        "first_name": row["first_name"],
        "surname":    row["surname"],
    }


def get_user_secret_question(email: str) -> Optional[str]:
    """
    Return the secret question for a registered, active account.

    Args:
        email: The user's email address.

    Returns:
        The secret question string, or None if email not found or account inactive.
    """
    sql = """
        SELECT uc.secret_question
        FROM user_credentials uc
        JOIN users u ON u.user_id = uc.user_id
        WHERE u.email = %s
          AND u.is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row["secret_question"] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Verify the secret answer for a registered, active account (case-insensitive).

    Args:
        email:  The user's email address.
        answer: The plaintext answer to verify (compared case-insensitively).

    Returns:
        True if the answer matches, False otherwise.
    """
    sql = """
        SELECT uc.secret_answer_hash
        FROM user_credentials uc
        JOIN users u ON u.user_id = uc.user_id
        WHERE u.email = %s
          AND u.is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()

    if row is None:
        return False

    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
    ph = PasswordHasher()
    try:
        ph.verify(row["secret_answer_hash"], answer.lower())  # seed hashed with .lower()
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def update_password(email: str, new_password: str) -> bool:
    """
    Update the hashed password for an active account.
    Caller is responsible for verifying identity before calling this function.

    Args:
        email:        The user's email address.
        new_password: The new plaintext password to hash and store.

    Returns:
        True if the password was updated, False if email not found or account inactive.
    """
    # Fetch user_id first so we only UPDATE credentials for active accounts
    sql_find = """
        SELECT u.user_id
        FROM users u
        WHERE u.email = %s
          AND u.is_active = TRUE
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_find, (email,))
            row = cur.fetchone()

    if row is None:
        return False

    from argon2 import PasswordHasher
    ph = PasswordHasher()
    new_hash = ph.hash(new_password)

    sql_update = """
        UPDATE user_credentials
        SET password_hash = %s
        WHERE user_id = %s
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_update, (new_hash, row["user_id"]))
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
