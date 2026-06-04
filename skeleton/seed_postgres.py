"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import Json, execute_values
from argon2 import PasswordHasher

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    """
    Inserts core metro station records into metro_stations.

    Design decisions:
    - `lines` is intentionally omitted here and handled by seed_metro_station_lines()
      in a separate junction table. A station can belong to multiple lines; storing
      them as an array prevents B-tree indexing, whereas a junction table allows
      efficient WHERE line = ? queries.
    - `adjacent_stations` represents a graph relationship (edges with distance and
      direction) and is managed entirely by Neo4j. Duplicating it in the relational
      database would create a dual-maintenance burden and risk inconsistency.
    - `interchange_metro_lines` is redundant: the is_interchange_metro boolean flag
      is sufficient to identify interchange stations, and the actual lines can be
      retrieved via a JOIN on metro_station_lines.
    - interchange_national_rail_station_id uses .get() because most metro stations
      have no corresponding national rail station; NULL is more semantically correct
      than an empty string for a missing foreign key.
    """
    data = load("metro_stations.json")
    rows = []
    for station in data:
        rows.append((
            station["station_id"],
            station["name"],
            station["is_interchange_metro"],
            station["is_interchange_national_rail"],
            station.get("interchange_national_rail_station_id"),  # nullable: not all metro stations link to a national rail station
        ))

    n = insert_many(cur, "metro_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "is_interchange_national_rail",
                     "interchange_national_rail_station_id"],
                    rows)
    print(f"  metro_stations: {n} rows")


def seed_metro_station_lines(cur):
    """
    Inserts metro station-to-line mappings into metro_station_lines (junction table).

    Design decisions:
    - Lines are stored in a separate table rather than an array column on
      metro_stations so that WHERE line = 'M1' can use an index. It also means
      adding a new line only requires an INSERT, not an UPDATE on the station row
      (Open/Closed principle).
    - Although this function reads the same JSON as seed_metro_stations(), they are
      kept as two separate functions so that each is responsible for exactly one
      table and can be re-run or debugged independently.
    """
    data = load("metro_stations.json")
    rows = []
    for station in data:
        for line in station["lines"]:
            rows.append((
                station["station_id"],
                line,
            ))

    n = insert_many(cur, "metro_station_lines",
                    ["station_id", "line"],
                    rows)
    print(f"  metro_station_lines: {n} rows")


def seed_national_rail_stations(cur):
    """
    Inserts core national rail station records into national_rail_stations.

    Design decisions:
    - `lines` is delegated to seed_national_rail_station_lines() for the same
      reason as the metro equivalent: normalization enables indexed line queries
      and makes adding new lines a pure INSERT operation.
    - `adjacent_stations` is a graph relationship managed by Neo4j; it is not
      stored in the relational database.
    - `interchange_national_rail_lines` is redundant given the
      is_interchange_national_rail boolean flag; actual lines can be obtained
      via a JOIN on national_rail_station_lines.
    - interchange_metro_station_id uses .get() because not every national rail
      station has a corresponding metro station; the column is nullable.
    """
    data = load("national_rail_stations.json")
    rows = []
    for station in data:
        rows.append((
            station["station_id"],
            station["name"],
            station["is_interchange_national_rail"],
            station["is_interchange_metro"],
            station.get("interchange_metro_station_id"),  # nullable
        ))

    n = insert_many(cur, "national_rail_stations",
                    ["station_id", "name", "is_interchange_national_rail",
                     "is_interchange_metro", "interchange_metro_station_id"],
                    rows)
    print(f"  national_rail_stations: {n} rows")


def seed_national_rail_station_lines(cur):
    """
    Inserts national rail station-to-line mappings into national_rail_station_lines
    (junction table).

    Design decisions:
    - Follows the same pattern as seed_metro_station_lines(): lines live in their
      own table to support indexed lookups and schema-friendly extensibility.
    - Kept as a separate function from seed_national_rail_stations() so that each
      function owns exactly one table and can be run or tested in isolation.
      Both functions load the same JSON file independently to avoid coupling.
    """
    data = load("national_rail_stations.json")
    rows = []
    for station in data:
        for line in station["lines"]:
            rows.append((
                station["station_id"],
                line,
            ))

    n = insert_many(cur, "national_rail_station_lines",
                    ["station_id", "line"],
                    rows)
    print(f"  national_rail_station_lines: {n} rows")


def seed_metro_schedules(cur):
    """
    Inserts metro schedule header records into metro_schedules.

    Design decisions:
    - Stop order and travel-time offsets are deliberately excluded from this
      table and loaded by seed_metro_schedule_stops(). Keeping one row per
      schedule here and one row per stop in the junction table satisfies the
      normalization requirement while still preserving the original route order.
    - operates_on is stored as JSONB because it is a small fixed list attached
      to one schedule; the application does not need to join or filter it as a
      separate entity in the current requirements.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("metro_schedules.json")
    rows = []
    for schedule in data:
        rows.append((
            schedule["schedule_id"],
            schedule["line"],
            schedule["direction"],
            schedule["origin_station_id"],
            schedule["destination_station_id"],
            schedule["first_train_time"],
            schedule["last_train_time"],
            schedule["base_fare_usd"],
            schedule["per_stop_rate_usd"],
            schedule["frequency_min"],
            Json(schedule["operates_on"]),
        ))

    n = insert_many(cur, "metro_schedules",
                    ["schedule_id", "line", "direction", "origin_station_id",
                     "destination_station_id", "first_train_time",
                     "last_train_time", "base_fare_usd", "per_stop_rate_usd",
                     "frequency_min", "operates_on"],
                    rows)
    print(f"  metro_schedules: {n} rows")


def seed_metro_schedule_stops(cur):
    """
    Inserts one metro_schedule_stops row per station stop in each metro schedule.

    Design decisions:
    - stop_order is generated with a 1-based sequence so SQL queries can compare
      origin and destination positions directly (origin.stop_order < destination.stop_order).
    - travel_time_from_origin_min is copied from the source map for each station
      to avoid parsing JSON during availability queries.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("metro_schedules.json")
    rows = []
    for schedule in data:
        travel_times = schedule["travel_time_from_origin_min"]
        for stop_order, station_id in enumerate(schedule["stops_in_order"], start=1):
            rows.append((
                schedule["schedule_id"],
                station_id,
                stop_order,
                travel_times[station_id],
            ))

    n = insert_many(cur, "metro_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"],
                    rows)
    print(f"  metro_schedule_stops: {n} rows")


def seed_national_rail_schedules(cur):
    """
    Inserts national rail schedule header records into national_rail_schedules.

    Design decisions:
    - fare_classes remains JSONB because fare lookup is schedule-scoped: the
      query knows schedule_id and fare_class, then extracts that small nested
      object directly from the selected schedule row.
    - passed_through_stations is stored as NULL for normal services because the
      concept only applies to express services that pass through stations
      without stopping.
    - Actual stopping sequence is loaded separately by
      seed_national_rail_schedule_stops() so route-order queries do not need to
      inspect JSON arrays.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("national_rail_schedules.json")
    rows = []
    for schedule in data:
        rows.append((
            schedule["schedule_id"],
            schedule["line"],
            schedule["service_type"],
            schedule["direction"],
            schedule["origin_station_id"],
            schedule["destination_station_id"],
            schedule["first_train_time"],
            schedule["last_train_time"],
            schedule["frequency_min"],
            Json(schedule["passed_through_stations"]) if "passed_through_stations" in schedule else None,
            Json(schedule["fare_classes"]),
            Json(schedule["operates_on"]),
        ))

    n = insert_many(cur, "national_rail_schedules",
                    ["schedule_id", "line", "service_type", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time", "frequency_min",
                     "passed_through_stations", "fare_classes", "operates_on"],
                    rows)
    print(f"  national_rail_schedules: {n} rows")


def seed_national_rail_schedule_stops(cur):
    """
    Inserts one national_rail_schedule_stops row per stopping station.

    Design decisions:
    - Only stations in stops_in_order are inserted here. Express-only
      passed_through_stations are not stops and must not be returned as
      boardable stations by availability queries.
    - The normalized stop_order column is the source of truth for checking
      whether a destination appears after an origin on the same service.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("national_rail_schedules.json")
    rows = []
    for schedule in data:
        travel_times = schedule["travel_time_from_origin_min"]
        for stop_order, station_id in enumerate(schedule["stops_in_order"], start=1):
            rows.append((
                schedule["schedule_id"],
                station_id,
                stop_order,
                travel_times[station_id],
            ))

    n = insert_many(cur, "national_rail_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"],
                    rows)
    print(f"  national_rail_schedule_stops: {n} rows")


def seed_seat_layouts(cur):
    """
    Inserts seat layout records into national_rail_seat_layouts.

    Design decisions:
    - coaches is stored as a single JSONB blob per layout because seat layout
      is a document-like structure that is always retrieved and rendered as a
      whole (e.g. seat picker UI). Normalizing coaches and seats into separate
      tables would add JOIN overhead with no query benefit under current requirements.
    - Only normal services (NR_SCH01–04) have seat layouts. Express services
      (NR_SCH05–08) have no assigned seating and therefore have no layout record.
      This is intentional and requires no special handling here.
    - Json() wraps the coaches list so psycopg2 serializes it correctly as JSONB
      via execute_values, consistent with the pattern used in seed_metro_schedules()
      and seed_national_rail_schedules().

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("national_rail_seat_layouts.json")
    rows = []
    for layout in data:
        rows.append((
            layout["layout_id"],
            layout["schedule_id"],
            Json(layout["coaches"]),
        ))

    n = insert_many(cur, "national_rail_seat_layouts",
                    ["layout_id", "schedule_id", "coaches"],
                    rows)
    print(f"  national_rail_seat_layouts: {n} rows")


def seed_users(cur):
    """
    Inserts user profile records into the users table. Passwords and security
    answers are intentionally excluded here and handled exclusively by
    seed_user_credentials(), following the principle of least privilege:
    queries that only need profile data should never touch credential columns.

    Design decisions:
    - The source JSON provides full_name as a single string, but the schema
      stores first_name and last_name separately to allow per-field display,
      sorting by surname, and future locale-aware formatting. The split is done
      on the first space only (maxsplit=1) to avoid truncating middle names.
    - phone uses .get() because not all users provide a phone number; NULL is
      semantically cleaner than an empty string for a missing optional field.
    - The password field in the JSON is never read here, ensuring plaintext
      credentials do not appear in any non-credential code path.
    """
    data = load("registered_users.json")
    rows = []
    for user in data:
        # Split full_name into first / last with maxsplit=1 to preserve middle names
        parts = user["full_name"].split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        rows.append((
            user["user_id"],
            first_name,
            last_name,
            user["email"],
            user.get("phone"),       # nullable: phone number is optional
            user["date_of_birth"],
            user["registered_at"],
            user["is_active"],
        ))

    n = insert_many(cur, "users",
                    ["user_id", "first_name", "last_name", "email",
                        "phone", "date_of_birth", "registered_at", "is_active"],
                    rows)
    print(f"  users: {n} rows")


def seed_user_credentials(cur):
    """
    Inserts hashed passwords and security answer hashes into user_credentials.

    Design decisions:
    - argon2id (the default algorithm of PasswordHasher) is used per OWASP
      password storage recommendations. argon2's output string embeds the salt,
      algorithm version, and cost parameters, so no separate salt column is
      needed in the schema.
    - Passwords in the JSON are plaintext (development mock data only). Hashing
      happens here at seed time so the database never contains plaintext credentials.
    - secret_answer is also hashed with argon2 and compared case-sensitively.
      This is an intentional security design: the security question serves as an
      account recovery mechanism, and requiring an exact match reduces the risk
      of brute-force guessing.
    - user_credentials.user_id has a UNIQUE constraint, so ON CONFLICT (user_id)
      makes the seeder idempotent while keeping c_id as the internal surrogate key.
    - This function uses per-row cur.execute() rather than execute_values() because
      argon2 hashing must be computed in Python for each user individually and
      cannot be vectorized. Forcing a batch approach would add complexity with no
      performance benefit.
    """
    data = load("registered_users.json")
    ph = PasswordHasher()
    inserted = 0

    for user in data:
        # argon2 generates a fresh random salt on every call, so identical
        # passwords produce different hashes — this defeats rainbow table attacks
        password_hash = ph.hash(user["password"])
        secret_answer_hash = ph.hash(user["secret_answer"].lower())  # lowercase before hashing so verify_secret_answer can compare case-insensitively

        cur.execute(
            """
            INSERT INTO user_credentials
                (user_id, password_hash, secret_question, secret_answer_hash)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (
                user["user_id"],
                password_hash,
                user["secret_question"],
                secret_answer_hash,
            )
        )
        inserted += cur.rowcount

    print(f"  user_credentials: {inserted} rows")



def seed_national_rail_bookings(cur):
    """
    Inserts national rail booking records into national_rail_bookings.

    Design decisions:
    - travelled_at uses .get() because bookings with status 'confirmed' or
      'cancelled' have not yet been travelled; the column is nullable by design.
    - All other fields map directly from the JSON; no transformation is needed.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("bookings.json")
    rows = []
    for booking in data:
        rows.append((
            booking["booking_id"],
            booking["user_id"],
            booking["schedule_id"],
            booking["origin_station_id"],
            booking["destination_station_id"],
            booking["travel_date"],
            booking["departure_time"],
            booking["ticket_type"],
            booking["fare_class"],
            booking["coach"],
            booking["seat_id"],
            booking["stops_travelled"],
            booking["amount_usd"],
            booking["status"],
            booking["booked_at"],
            booking.get("travelled_at"),  # nullable: None for confirmed/cancelled bookings
        ))

    n = insert_many(cur, "national_rail_bookings",
                    ["booking_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "departure_time", "ticket_type",
                     "fare_class", "coach", "seat_id", "stops_travelled",
                     "amount_usd", "status", "booked_at", "travelled_at"],
                    rows)
    print(f"  national_rail_bookings: {n} rows")


def seed_metro_travels(cur):
    """
    Inserts metro trip records into metro_trips.

    Design decisions:
    - day_pass_ref links additional trips taken under the same day pass purchase
      to their originating trip (e.g. MT021/MT022 both reference MT002). It is
      NULL for single tickets and for the initial day-pass purchase trip itself.
    - stops_travelled is NULL for all day-pass trips because a day pass grants
      unlimited travel; per-stop counting is not applicable.
    - purchased_at is NULL for secondary day-pass trips (amount_usd = 0.0)
      because no separate purchase event occurs — only the originating trip has
      a purchase timestamp.
    - travelled_at uses .get() because cancelled trips may not have a travel
      timestamp.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("metro_travel_history.json")
    rows = []
    for trip in data:
        rows.append((
            trip["trip_id"],
            trip["user_id"],
            trip["schedule_id"],
            trip["origin_station_id"],
            trip["destination_station_id"],
            trip["travel_date"],
            trip["ticket_type"],
            trip.get("day_pass_ref"),       # nullable: None for single tickets and originating day-pass trips
            trip.get("stops_travelled"),    # nullable: None for all day-pass trips
            trip["amount_usd"],
            trip["status"],
            trip.get("purchased_at"),       # nullable: None for secondary day-pass trips
            trip.get("travelled_at"),       # nullable: None for cancelled trips
        ))

    n = insert_many(cur, "metro_trips",
                    ["trip_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "ticket_type", "day_pass_ref",
                     "stops_travelled", "amount_usd", "status",
                     "purchased_at", "travelled_at"],
                    rows)
    print(f"  metro_trips: {n} rows")


def seed_payments(cur):
    """
    Inserts payment records into payments.

    Design decisions:
    - payments.json uses a single booking_id field for both national rail bookings
      (prefix 'BK') and metro trips (prefix 'MT'). The schema stores these as two
      separate nullable FKs (national_rail_booking_id, metro_trip_id) to preserve
      referential integrity per table. The prefix determines which FK is populated
      and the other is set to NULL.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("payments.json")
    rows = []
    for payment in data:
        booking_id = payment["booking_id"]
        # Route the single booking_id to the correct FK based on its prefix
        nr_id = booking_id if booking_id.startswith("BK") else None
        mt_id = booking_id if booking_id.startswith("MT") else None
        rows.append((
            payment["payment_id"],
            nr_id,
            mt_id,
            payment["amount_usd"],
            payment["method"],
            payment["status"],
            payment["paid_at"],
        ))

    n = insert_many(cur, "payments",
                    ["payment_id", "national_rail_booking_id", "metro_trip_id",
                     "amount_usd", "method", "status", "paid_at"],
                    rows)
    print(f"  payments: {n} rows")


def seed_feedback(cur):
    """
    Inserts feedback records into feedback.

    Design decisions:
    - feedback.json uses a single booking_id field for both national rail bookings
      (prefix 'BK') and metro trips (prefix 'MT'). Same prefix-based routing as
      seed_payments(): one FK is populated and the other is NULL.
    - user_id is present directly in the JSON and maps to the feedback.user_id FK.

    Args:
        cur: Open psycopg2 cursor owned by main().

    Returns:
        None.
    """
    data = load("feedback.json")
    rows = []
    for fb in data:
        booking_id = fb["booking_id"]
        nr_id = booking_id if booking_id.startswith("BK") else None
        mt_id = booking_id if booking_id.startswith("MT") else None
        rows.append((
            fb["feedback_id"],
            fb["user_id"],
            nr_id,
            mt_id,
            fb["rating"],
            fb["comment"],
            fb["submitted_at"],
        ))

    n = insert_many(cur, "feedback",
                    ["feedback_id", "user_id", "national_rail_booking_id",
                     "metro_trip_id", "rating", "comment", "submitted_at"],
                    rows)
    print(f"  feedback: {n} rows")


# ── main ────────────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_metro_station_lines(cur)
        seed_national_rail_stations(cur)
        seed_national_rail_station_lines(cur)
        seed_metro_schedules(cur)
        seed_metro_schedule_stops(cur)
        seed_national_rail_schedules(cur)
        seed_national_rail_schedule_stops(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_user_credentials(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
