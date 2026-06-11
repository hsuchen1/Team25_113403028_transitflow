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


# SCHEMA RETROFIT: business IDs (booking_id, payment_id, ...) now follow the
# same convention as user_id — a zero-padded number derived from the table's
# `id SERIAL` sequence (e.g. id=21 -> "BK021"), matching the seed data format
# (BK001, PM001, ...) instead of the previous random "BK-XXXXXX" suffix.
def _gen_id(cur, table: str, prefix: str) -> tuple[int, str]:
    """
    Reserve the next surrogate `id` value for `table` and format a business
    identifier from it, e.g. _gen_id(cur, "national_rail_bookings", "BK")
    -> (21, "BK021"). The returned int must be inserted into the row's `id`
    column so the surrogate PK and the business ID stay in sync.
    """
    cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id')) AS nid", (table,))
    next_id = cur.fetchone()["nid"]
    return next_id, f"{prefix}{next_id:03d}"


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
    departure_time: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    # TASK 6 EXTENSION: a schedule_id can run multiple departures per day (one
    # every frequency_min), and each departure has its own independent seat pool.
    # If departure_time is given (selected from get_departure_times),
    # available_seats reflects that specific departure's occupancy. If omitted,
    # available_seats falls back to a schedule+date-wide count (legacy
    # behaviour — treats every departure of the day as one shared pool, which
    # is an approximation, not the true per-train availability).

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to filter by day and count bookings;
                         omit for general info (available_seats will be NULL).
        departure_time:  optional, e.g. "07:00" — scopes available_seats to a
                         single departure of the schedule. Ignored if travel_date
                         is not also given.

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
    departure_filter = "AND b.departure_time = %s::time" if (travel_date and departure_time) else ""
    available_seats_expr = f"""
        (
            SELECT (
                -- Count total seats across all coaches in the layout
                SELECT COALESCE(SUM(jsonb_array_length(coach -> 'seats')), 0)
                FROM national_rail_seat_layouts sl,
                     jsonb_array_elements(sl.coaches) AS coach
                WHERE sl.schedule_id = s.schedule_id
            ) - (
                -- Subtract seats already booked on the requested date (and,
                -- if departure_time is given, that specific departure only)
                SELECT COUNT(*)
                FROM national_rail_bookings b
                WHERE b.schedule_id = s.schedule_id
                  AND b.travel_date = %s::date
                  {departure_filter}
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
        if departure_time:
            params.insert(1, departure_time)  # for available_seats subquery

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
    departure_time: Optional[str] = None,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    # TASK 6 EXTENSION: a schedule_id can run multiple departures per day
    # (one every frequency_min), and each departure has its own independent
    # seat pool. If departure_time is given, only bookings on that exact
    # departure are excluded. If omitted, the check falls back to the legacy
    # schedule+date-wide behaviour (less precise — treats every departure of
    # the day as sharing one seat pool).

    Args:
        schedule_id:    e.g. "NR_SCH01"
        travel_date:    e.g. "2025-06-01"
        fare_class:     "standard" or "first"
        departure_time: optional, e.g. "07:00" — selects the specific train
                         (from get_departure_times) so seat availability is
                         computed for that exact departure only.

    Returns:
        List of dicts: {seat_id, coach, row, column}.
        Empty list if no seats available or schedule has no layout.
    """
    departure_filter = "AND departure_time = %s::time" if departure_time else ""
    sql = f"""
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
              -- Exclude seats already booked on this date (and, if given, this departure)
              SELECT seat_id
              FROM national_rail_bookings
              WHERE schedule_id = %s
                AND travel_date = %s::date
                {departure_filter}
                AND status != 'cancelled'
          )
        ORDER BY (seat ->> 'row')::integer, seat ->> 'column'
    """
    params = [schedule_id, fare_class, schedule_id, travel_date]
    if departure_time:
        params.append(departure_time)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
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


# ── DEPARTURE TIMES ──────────────────────────────────────────────────────────

def query_departure_times(schedule_id: str, boarding_station_id: Optional[str] = None) -> list[dict]:
    """
    Return all valid departure times for a national rail schedule.
    Departure time is defined as the time the train departs from the schedule's
    origin station (first stop). Times are generated from first_train_time to
    last_train_time at frequency_min intervals.

    # TASK 6 EXTENSION: if boarding_station_id is given, also compute an
    # ESTIMATED arrival time at that station (departure_time + travel_time_from_origin_min).
    # This is an estimate derived from schedule data, not a guaranteed real-time arrival.

    Args:
        schedule_id: e.g. "NR_SCH01"
        boarding_station_id: optional station the user will board at; if provided
            and present on this schedule's stop list, each entry also includes
            estimated_arrival_at_boarding_station and a note marking it as estimated.

    Returns:
        List of dicts with departure_time (HH:MM string) and schedule metadata.
        Empty list if schedule not found.
    """
    from datetime import datetime, timedelta

    sql = """
        SELECT schedule_id, first_train_time, last_train_time, frequency_min,
               line, service_type, direction
        FROM national_rail_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

            if row is None:
                return []

            travel_time_from_origin_min = None
            if boarding_station_id:
                cur.execute(
                    """
                    SELECT travel_time_from_origin_min
                    FROM national_rail_schedule_stops
                    WHERE schedule_id = %s AND station_id = %s
                    """,
                    (schedule_id, boarding_station_id),
                )
                stop_row = cur.fetchone()
                if stop_row is not None:
                    travel_time_from_origin_min = stop_row["travel_time_from_origin_min"]

    first = datetime.strptime(str(row["first_train_time"]), "%H:%M:%S")
    last  = datetime.strptime(str(row["last_train_time"]),  "%H:%M:%S")
    freq  = row["frequency_min"]

    times = []
    current = first
    while current <= last:
        entry = {
            "departure_time": current.strftime("%H:%M"),
            "schedule_id":    row["schedule_id"],
            "line":           row["line"],
            "service_type":   row["service_type"],
            "direction":      row["direction"],
        }
        if travel_time_from_origin_min is not None:
            estimated = current + timedelta(minutes=travel_time_from_origin_min)
            entry["estimated_arrival_at_boarding_station"] = estimated.strftime("%H:%M")
            entry["note"] = (
                "estimated_arrival_at_boarding_station is an ESTIMATE based on the "
                "schedule's travel time from the origin station; actual arrival may vary."
            )
        times.append(entry)
        current += timedelta(minutes=freq)

    return times


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    departure_time: Optional[str] = None,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking atomically (booking + payment in one transaction).

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        departure_time:         e.g. "07:00" — train departure from schedule's first station.
                                If None, falls back to schedule's first_train_time.
        ticket_type:            "single" (default) or "return"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    from datetime import datetime, timezone
    import random, string

    # ── 1. Fetch schedule info (fare, stops, departure_time fallback/validation) ──
    # TASK 6 EXTENSION: also fetch last_train_time, frequency_min, and operates_on
    # so a caller-supplied departure_time can be validated against the real timetable.
    sql_schedule = """
        SELECT s.first_train_time, s.last_train_time, s.frequency_min, s.operates_on,
               s.fare_classes,
               orig.stop_order AS orig_order,
               dest.stop_order AS dest_order
        FROM national_rail_schedules s
        JOIN national_rail_schedule_stops orig ON orig.schedule_id = s.schedule_id
            AND orig.station_id = %s
        JOIN national_rail_schedule_stops dest ON dest.schedule_id = s.schedule_id
            AND dest.station_id = %s
        WHERE s.schedule_id = %s
    """

    # ── 2. Auto-assign seat if "any" ───────────────────────────────────────────
    # TASK 6 EXTENSION: a schedule_id + travel_date can have many physical trains
    # (one every frequency_min), each with its own independent seat pool. The
    # NOT IN subquery now also filters on departure_time so seats taken on a
    # different departure of the same schedule/date are not treated as taken here.
    sql_available_seat = """
        SELECT seat ->> 'seat_id' AS seat_id
        FROM national_rail_seat_layouts sl,
             jsonb_array_elements(sl.coaches) AS coach,
             jsonb_array_elements(coach -> 'seats') AS seat
        WHERE sl.schedule_id = %s
          AND coach ->> 'fare_class' = %s
          AND seat ->> 'seat_id' NOT IN (
              SELECT seat_id FROM national_rail_bookings
              WHERE schedule_id = %s AND travel_date = %s::date
                AND departure_time = %s::time AND status != 'cancelled'
          )
        LIMIT 1
    """

    sql_insert_booking = """
        INSERT INTO national_rail_bookings
            (id, booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
             travel_date, departure_time, ticket_type, fare_class, coach, seat_id,
             stops_travelled, amount_usd, status, booked_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s::date, %s::time, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
    """

    sql_insert_payment = """
        INSERT INTO payments
            (id, payment_id, national_rail_booking_id, amount_usd, method, status, paid_at)
        VALUES (%s, %s, %s, %s, 'credit_card', 'paid', %s)
    """

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # Fetch schedule + stop order
            cur.execute(sql_schedule, (origin_station_id, destination_station_id, schedule_id))
            sched = cur.fetchone()
            if sched is None:
                return False, "Schedule or stations not found."

            stops_travelled = sched["dest_order"] - sched["orig_order"]

            # Resolve departure_time: use provided value or fall back to first_train_time
            dep_time = departure_time if departure_time else str(sched["first_train_time"])[:5]

            # ── Validate departure_time against the schedule's timetable ───────
            # TASK 6 EXTENSION: execute_booking previously accepted any
            # departure_time string. Now it must align with first_train_time +
            # N * frequency_min and not exceed last_train_time — i.e. it must be
            # one of the times query_departure_times would generate.
            first_dt = datetime.strptime(str(sched["first_train_time"]), "%H:%M:%S")
            last_dt = datetime.strptime(str(sched["last_train_time"]), "%H:%M:%S")
            freq = sched["frequency_min"]

            if departure_time:
                try:
                    dep_dt = datetime.strptime(departure_time, "%H:%M")
                except ValueError:
                    return False, "Invalid departure_time format; expected HH:MM."

                if dep_dt < first_dt or dep_dt > last_dt:
                    return False, (
                        f"Invalid departure_time {departure_time}: schedule {schedule_id} "
                        f"runs from {first_dt.strftime('%H:%M')} to {last_dt.strftime('%H:%M')}."
                    )

                elapsed_min = (dep_dt - first_dt).total_seconds() / 60
                if elapsed_min % freq != 0:
                    return False, (
                        f"Invalid departure_time {departure_time}: does not align with "
                        f"schedule {schedule_id}'s frequency ({freq}-minute intervals from "
                        f"{first_dt.strftime('%H:%M')}). Use get_departure_times to see valid times."
                    )

            # ── Validate the schedule operates on the requested travel_date ────
            travel_dow = datetime.strptime(travel_date, "%Y-%m-%d").strftime("%a").lower()
            operates_on = sched["operates_on"]
            if operates_on is not None and travel_dow not in operates_on:
                return False, f"Schedule {schedule_id} does not operate on {travel_dow} ({travel_date})."

            # Resolve seat
            actual_seat_id = seat_id
            coach = None
            if seat_id.lower() == "any":
                cur.execute(sql_available_seat, (schedule_id, fare_class, schedule_id, travel_date, dep_time))
                seat_row = cur.fetchone()
                if seat_row is None:
                    return False, "No available seats for this fare class."
                actual_seat_id = seat_row["seat_id"]

            # Determine coach from seat_id (first character)
            coach = actual_seat_id[0] if actual_seat_id else None

            # Calculate fare
            fare_classes = sched  # fare_classes is in the schedule
            sql_fare = """
                SELECT fare_classes -> %s -> 'base_fare_usd'    AS base,
                       fare_classes -> %s -> 'per_stop_rate_usd' AS per_stop
                FROM national_rail_schedules WHERE schedule_id = %s
            """
            cur.execute(sql_fare, (fare_class, fare_class, schedule_id))
            fare_row = cur.fetchone()
            if fare_row is None or fare_row["base"] is None:
                return False, f"Invalid fare class: {fare_class}"
            amount = round(float(fare_row["base"]) + float(fare_row["per_stop"]) * stops_travelled, 2)

            # Generate IDs (kept in sync with the new id SERIAL PK — see _gen_id)
            booking_seq, booking_id = _gen_id(cur, "national_rail_bookings", "BK")
            payment_seq, payment_id = _gen_id(cur, "payments", "PM")
            now = datetime.now(timezone.utc)

            # Check seat not already taken on this specific departure
            # TASK 6 EXTENSION: scoped to departure_time as well as schedule_id +
            # travel_date, since a schedule can run multiple departures per day
            # and each departure has its own independent seat pool.
            cur.execute("""
                SELECT 1 FROM national_rail_bookings
                WHERE schedule_id = %s AND travel_date = %s::date
                  AND departure_time = %s::time
                  AND seat_id = %s AND status != 'cancelled'
            """, (schedule_id, travel_date, dep_time, actual_seat_id))
            if cur.fetchone():
                return False, f"Seat {actual_seat_id} is already booked for this date and departure time."

            # Insert booking and payment atomically
            cur.execute(sql_insert_booking, (
                booking_seq, booking_id, user_id, schedule_id,
                origin_station_id, destination_station_id,
                travel_date, dep_time, ticket_type, fare_class,
                coach, actual_seat_id, stops_travelled, amount, now
            ))
            cur.execute(sql_insert_payment, (
                payment_seq, payment_id, booking_id, amount, now
            ))

        conn.commit()
        return True, {
            "booking_id":    booking_id,
            "user_id":       user_id,
            "schedule_id":   schedule_id,
            "seat_id":       actual_seat_id,
            "coach":         coach,
            "fare_class":    fare_class,
            "departure_time": dep_time,
            "travel_date":   travel_date,
            "amount_usd":    amount,
            "status":        "confirmed",
        }

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False, "Booking conflict — please try again."
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type
    and how many hours remain before the scheduled departure:
      - Normal service (RF001):  >=48h 100% (no fee) / 24-48h 75% ($0.50) /
                                  2-24h 50% ($0.50) / <2h or past 0% (no fee)
      - Express service (RF002): >=48h 100% ($1.00) / 24-48h 50% ($1.00) /
                                  <24h or past 0% (no fee)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy info
        (False, error_msg)   if the booking does not exist, does not belong
                              to this user, or is already cancelled
    """
    from datetime import datetime

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT b.booking_id, b.status, b.travel_date, b.departure_time,
                       b.amount_usd, s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s AND b.user_id = %s
                FOR UPDATE
                """,
                (booking_id, user_id),
            )
            booking = cur.fetchone()

            if booking is None:
                return False, "Booking not found."

            if booking["status"] == "cancelled":
                return False, "Booking is already cancelled."

            departure_dt = datetime.combine(booking["travel_date"], booking["departure_time"])
            hours_before = (departure_dt - datetime.now()).total_seconds() / 3600

            if booking["service_type"] == "express":
                policy_id = "RF002"
                if hours_before >= 48:
                    refund_percent, admin_fee = 100, 1.00
                elif hours_before >= 24:
                    refund_percent, admin_fee = 50, 1.00
                else:
                    refund_percent, admin_fee = 0, 0.00
            else:
                policy_id = "RF001"
                if hours_before >= 48:
                    refund_percent, admin_fee = 100, 0.00
                elif hours_before >= 24:
                    refund_percent, admin_fee = 75, 0.50
                elif hours_before >= 2:
                    refund_percent, admin_fee = 50, 0.50
                else:
                    refund_percent, admin_fee = 0, 0.00

            amount = float(booking["amount_usd"])
            refund_amount = max(round(amount * refund_percent / 100 - admin_fee, 2), 0.0)

            cur.execute(
                "UPDATE national_rail_bookings SET status = 'cancelled' WHERE booking_id = %s",
                (booking_id,),
            )

        conn.commit()
        return True, {
            "booking_id": booking_id,
            "status": "cancelled",
            "policy_id": policy_id,
            "hours_before_departure": round(hours_before, 2),
            "refund_percent": refund_percent,
            "admin_fee_usd": admin_fee,
            "refund_amount_usd": refund_amount,
        }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


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
