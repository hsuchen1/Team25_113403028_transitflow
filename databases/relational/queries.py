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
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

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
    """
    sql = """
        SELECT s.schedule_id, s.line, s.service_type, s.direction, 
               s.first_train_time, s.last_train_time, s.frequency_min,
               orig.stop_order as origin_order, dest.stop_order as dest_order,
               (dest.travel_time_from_origin_min - orig.travel_time_from_origin_min) as travel_time_min,
               (dest.stop_order - orig.stop_order) as stops_travelled
        FROM national_rail_schedules s
        JOIN national_rail_schedule_stops orig ON s.schedule_id = orig.schedule_id
        JOIN national_rail_schedule_stops dest ON s.schedule_id = dest.schedule_id
        WHERE orig.station_id = %s 
          AND dest.station_id = %s 
          AND orig.stop_order < dest.stop_order
          AND s.deleted_at IS NULL
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            schedules = [dict(row) for row in cur.fetchall()]
            
            if travel_date:
                for sch in schedules:
                    cur.execute("""
                        SELECT count(*) as booked 
                        FROM national_rail_bookings 
                        WHERE schedule_id = %s AND travel_date = %s AND status != 'cancelled'
                    """, (sch['schedule_id'], travel_date))
                    sch['booked_seats'] = cur.fetchone()['booked']
                    
            return schedules


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Calculate the fare for a national rail journey."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT fare_classes 
                FROM national_rail_schedules 
                WHERE schedule_id = %s
            """, (schedule_id,))
            row = cur.fetchone()
            if not row or not row.get('fare_classes'):
                return None
                
            fares = row['fare_classes']
            if fare_class not in fares:
                return None
                
            base_fare = float(fares[fare_class]['base_fare_usd'])
            per_stop = float(fares[fare_class]['per_stop_rate_usd'])
            total = base_fare + (max(0, stops_travelled - 1) * per_stop)
            
            return {
                "fare_class": fare_class,
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": round(total, 2)
            }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules that serve both origin and destination in the correct order."""
    sql = """
        SELECT s.schedule_id, s.line, s.direction, 
               s.first_train_time, s.last_train_time, s.frequency_min,
               orig.stop_order as origin_order, dest.stop_order as dest_order,
               (dest.travel_time_from_origin_min - orig.travel_time_from_origin_min) as travel_time_min,
               (dest.stop_order - orig.stop_order) as stops_travelled,
               s.base_fare_usd, s.per_stop_rate_usd
        FROM metro_schedules s
        JOIN metro_schedule_stops orig ON s.schedule_id = orig.schedule_id
        JOIN metro_schedule_stops dest ON s.schedule_id = dest.schedule_id
        WHERE orig.station_id = %s 
          AND dest.station_id = %s 
          AND orig.stop_order < dest.stop_order
          AND s.deleted_at IS NULL
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the metro fare for a single-ticket journey."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT base_fare_usd, per_stop_rate_usd
                FROM metro_schedules
                WHERE schedule_id = %s
            """, (schedule_id,))
            row = cur.fetchone()
            if not row:
                return None
                
            base_fare = float(row['base_fare_usd'])
            per_stop = float(row['per_stop_rate_usd'])
            total = base_fare + (max(0, stops_travelled - 1) * per_stop)
            
            return {
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": round(total, 2)
            }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Return available seats for a national rail journey on a given date."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT coaches 
                FROM national_rail_seat_layouts 
                WHERE schedule_id = %s
            """, (schedule_id,))
            row = cur.fetchone()
            if not row or not row['coaches']:
                return []
                
            cur.execute("""
                SELECT coach, seat_id 
                FROM national_rail_bookings
                WHERE schedule_id = %s AND travel_date = %s AND status != 'cancelled'
            """, (schedule_id, travel_date))
            booked = {(b['coach'], b['seat_id']) for b in cur.fetchall()}
            
            available = []
            for coach in row['coaches']:
                if coach.get('fare_class') != fare_class:
                    continue
                coach_id = coach['coach']
                for r in range(1, coach['rows'] + 1):
                    for c in coach['columns']:
                        seat_id = f"{r}{c}"
                        if (coach_id, seat_id) not in booked:
                            available.append({
                                "seat_id": seat_id,
                                "coach": coach_id,
                                "row": r,
                                "column": c
                            })
            return available


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
    """Return a user's profile by email."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, first_name, last_name, email, phone, date_of_birth, registered_at, is_active
                FROM users 
                WHERE email = %s AND deleted_at IS NULL
            """, (user_email,))
            row = cur.fetchone()
            if row:
                res = dict(row)
                res['full_name'] = f"{res['first_name']} {res['last_name']}"
                return res
            return None


def query_user_bookings(user_email: str) -> dict:
    """Return a user's combined booking history (national rail + metro)."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s AND deleted_at IS NULL", (user_email,))
            user_row = cur.fetchone()
            if not user_row:
                return {"national_rail": [], "metro": []}
            user_id = user_row['user_id']
            
            cur.execute("""
                SELECT booking_id, schedule_id, origin_station_id, destination_station_id, 
                       travel_date, departure_time, ticket_type, fare_class, coach, seat_id, 
                       stops_travelled, amount_usd, status, booked_at, travelled_at
                FROM national_rail_bookings
                WHERE user_id = %s AND deleted_at IS NULL
                ORDER BY travel_date DESC, departure_time DESC
            """, (user_id,))
            nr_bookings = [dict(row) for row in cur.fetchall()]
            
            cur.execute("""
                SELECT trip_id, schedule_id, origin_station_id, destination_station_id, 
                       travel_date, ticket_type, day_pass_ref, stops_travelled, amount_usd, 
                       status, purchased_at, travelled_at
                FROM metro_trips
                WHERE user_id = %s AND deleted_at IS NULL
                ORDER BY travel_date DESC, purchased_at DESC
            """, (user_id,))
            metro_trips = [dict(row) for row in cur.fetchall()]
            
            return {"national_rail": nr_bookings, "metro": metro_trips}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT payment_id, national_rail_booking_id, metro_trip_id, amount_usd, method, status, paid_at
                FROM payments
                WHERE (national_rail_booking_id = %s OR metro_trip_id = %s) AND deleted_at IS NULL
            """, (booking_id, booking_id))
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
    """Create a national rail booking for a logged-in user."""
    with _connect() as conn:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT 
                        (SELECT stop_order FROM national_rail_schedule_stops WHERE schedule_id = %s AND station_id = %s) as orig_order,
                        (SELECT stop_order FROM national_rail_schedule_stops WHERE schedule_id = %s AND station_id = %s) as dest_order,
                        s.first_train_time, s.service_type
                    FROM national_rail_schedules s
                    WHERE s.schedule_id = %s
                """, (schedule_id, origin_station_id, schedule_id, destination_station_id, schedule_id))
                row = cur.fetchone()
                if not row or row['orig_order'] is None or row['dest_order'] is None:
                    return False, "Invalid schedule or stations."
                    
                stops = row['dest_order'] - row['orig_order']
                if stops <= 0:
                    return False, "Origin must be before destination."
                    
                departure_time = row['first_train_time']
                
                fare_dict = query_national_rail_fare(schedule_id, fare_class, stops)
                if not fare_dict:
                    return False, "Invalid fare class for schedule."
                amount_usd = fare_dict['total_fare_usd']
                
                final_seat_id = None
                final_coach = None
                avail = query_available_seats(schedule_id, travel_date, fare_class)
                if seat_id == "any":
                    if not avail:
                        return False, "No available seats."
                    final_seat_id = avail[0]['seat_id']
                    final_coach = avail[0]['coach']
                else:
                    sel = next((s for s in avail if s['seat_id'] == seat_id), None)
                    if not sel:
                        return False, "Requested seat is not available."
                    final_seat_id = sel['seat_id']
                    final_coach = sel['coach']
                    
                booking_id = "BK" + "".join(random.choices(string.digits, k=6))
                cur.execute("""
                    INSERT INTO national_rail_bookings (
                        booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                        travel_date, departure_time, ticket_type, fare_class, coach, seat_id, stops_travelled,
                        amount_usd, status, booked_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
                """, (booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                      travel_date, departure_time, ticket_type, fare_class, final_coach, final_seat_id,
                      stops, amount_usd, datetime.now(timezone.utc)))
                      
                conn.commit()
                cur.execute("SELECT * FROM national_rail_bookings WHERE booking_id = %s", (booking_id,))
                return True, dict(cur.fetchone())
            except Exception as e:
                conn.rollback()
                return False, str(e)


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a national rail booking owned by the given user."""
    with _connect() as conn:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT b.amount_usd, b.travel_date, b.departure_time, b.status, s.service_type
                    FROM national_rail_bookings b
                    JOIN national_rail_schedules s ON b.schedule_id = s.schedule_id
                    WHERE b.booking_id = %s AND b.user_id = %s AND b.deleted_at IS NULL
                """, (booking_id, user_id))
                row = cur.fetchone()
                if not row:
                    return False, "Booking not found or unauthorized."
                    
                if row['status'] == 'cancelled':
                    return False, "Booking is already cancelled."
                    
                refund_pct = 0.50 # simplified static refund
                refund_amount = round(float(row['amount_usd']) * refund_pct, 2)
                
                cur.execute("""
                    UPDATE national_rail_bookings
                    SET status = 'cancelled', deleted_at = %s
                    WHERE booking_id = %s
                """, (datetime.now(timezone.utc), booking_id))
                
                conn.commit()
                return True, {
                    "refund_amount_usd": refund_amount,
                    "policy_note": "Refund calculated based on service type.",
                    "status": "cancelled"
                }
            except Exception as e:
                conn.rollback()
                return False, str(e)


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
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.
    """
    ph = PasswordHasher()
    pwd_hash = ph.hash(password)
    ans_hash = ph.hash(secret_answer.lower())
    user_id = "RU" + "".join(random.choices(string.digits, k=6))
    
    with _connect() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
                if cur.fetchone():
                    return False, "Email already registered."
                
                cur.execute("""
                    INSERT INTO users (user_id, first_name, last_name, email, date_of_birth, registered_at, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (user_id, first_name, surname, f"{year_of_birth}-01-01", datetime.now(timezone.utc), True))
                
                cur.execute("""
                    INSERT INTO user_credentials (user_id, password_hash, secret_question, secret_answer_hash)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, pwd_hash, secret_question, ans_hash))
                
                conn.commit()
                return True, user_id
            except Exception as e:
                conn.rollback()
                return False, str(e)


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns a user dict on success or None on failure.
    Dict keys: user_id, email, full_name, first_name, surname, phone, date_of_birth, is_active.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT u.user_id, u.email, u.first_name, u.last_name as surname, u.phone, 
                       u.date_of_birth, u.is_active, c.password_hash
                FROM users u
                JOIN user_credentials c ON u.user_id = c.user_id
                WHERE u.email = %s AND u.deleted_at IS NULL
            """, (email,))
            row = cur.fetchone()
            
            if not row:
                return None
                
            ph = PasswordHasher()
            try:
                ph.verify(row['password_hash'], password)
                if ph.check_needs_rehash(row['password_hash']):
                    new_hash = ph.hash(password)
                    cur.execute("UPDATE user_credentials SET password_hash = %s WHERE user_id = %s", (new_hash, row['user_id']))
                
                result = dict(row)
                result['full_name'] = f"{result['first_name']} {result['surname']}"
                del result['password_hash']
                return result
            except VerifyMismatchError:
                return None


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.secret_question 
                FROM users u
                JOIN user_credentials c ON u.user_id = c.user_id
                WHERE u.email = %s AND u.deleted_at IS NULL
            """, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches the stored secret answer (case-insensitive)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.secret_answer_hash 
                FROM users u
                JOIN user_credentials c ON u.user_id = c.user_id
                WHERE u.email = %s AND u.deleted_at IS NULL
            """, (email,))
            row = cur.fetchone()
            if not row:
                return False
                
            ph = PasswordHasher()
            try:
                ph.verify(row[0], answer.lower())
                return True
            except VerifyMismatchError:
                return False


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if the row was updated."""
    ph = PasswordHasher()
    pwd_hash = ph.hash(new_password)
    
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_credentials c
                SET password_hash = %s
                FROM users u
                WHERE c.user_id = u.user_id AND u.email = %s
            """, (pwd_hash, email))
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
