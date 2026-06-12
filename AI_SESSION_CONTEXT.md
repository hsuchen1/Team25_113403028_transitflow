# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Comment:** All Comments must be in English
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
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
    -- UNIQUE per FK: at most one payment per booking/trip (NULLs ignored by UNIQUE)
    national_rail_booking_id VARCHAR(20) UNIQUE REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) UNIQUE REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    amount_usd NUMERIC(10,2),
    method VARCHAR(50),
    status VARCHAR(20),
    paid_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    -- XOR: exactly one of the two FK targets is populated
    CHECK (num_nonnulls(national_rail_booking_id, metro_trip_id) = 1)
);

CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    feedback_id VARCHAR(20) UNIQUE NOT NULL,
    user_id VARCHAR(20) REFERENCES users(user_id) ON DELETE CASCADE,
    national_rail_booking_id VARCHAR(20) UNIQUE REFERENCES national_rail_bookings(booking_id) ON DELETE SET NULL,
    metro_trip_id VARCHAR(20) UNIQUE REFERENCES metro_trips(trip_id) ON DELETE SET NULL,
    rating INTEGER,
    comment TEXT,
    submitted_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    -- XOR as in payments; UNIQUE on each FK enforces 0..1 per booking/trip.
    -- execute_submit_feedback uses ON CONFLICT DO UPDATE (upsert).
    CHECK (num_nonnulls(national_rail_booking_id, metro_trip_id) = 1)
);
```

## Agreed Graph Schema

Node labels:
- `MetroStation` (Specific label for metro stations)
- `NationalRailStation` (Specific label for national rail stations)

Relationship types:
- `[:METRO_LINK]` (Properties: `line`, `travel_time_min`, `cost_usd`)
- `[:RAIL_LINK]` (Properties: `line`, `travel_time_min`, `cost_standard_usd`, `cost_first_usd`)
- `[:INTERCHANGE_TO]` (Properties: `travel_time_min` e.g. 5)

Key properties:
- Node: `station_id` (Unique constraint), `name`, `lines`, `is_interchange_metro`, `is_interchange_national_rail`
- Edge: `travel_time_min` (Used as weights for fastest route routing), and `cost_*_usd` properties (Used as weights for cheapest route routing)

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
def query_all_paths_between(origin_id: str, destination_id: str, network: str = "auto", limit: int = 5) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [x] Schema design:
  - **Decision:** Split `full_name` into `first_name` and `last_name` in `users` table. **Why:** Matches `register_user` API signature, improves search/sort by surname, and allows personalized UI greetings.
  - **Decision:** Added `id SERIAL PRIMARY KEY` to `users` table, while keeping `user_id` as `UNIQUE NOT NULL`. Chose Auto-Increment over UUID v7. **Why:** Auto-increment (`SERIAL`) was chosen over UUID v7 because it provides native, sequential ID generation without relying on external Python packages or PostgreSQL extensions (like `pg_uuidv7`). It also offers better index performance and lower storage overhead (4 bytes vs 16 bytes). Foreign keys continue to safely reference the unique `user_id`.
  - **Decision:** Refactored all tables to use Surrogate Keys (`id SERIAL PRIMARY KEY`) instead of VARCHAR primary keys, while keeping original business identifiers (e.g. `station_id`) as `UNIQUE NOT NULL`. **Why:** (1) **Surrogate vs Natural**: An integer primary key significantly improves B-tree index efficiency compared to wide VARCHAR keys, and decoupling the internal identity from external business identifiers prevents cascading updates if a business ID ever changes. (2) **SERIAL vs UUID**: We chose Auto-Increment (`SERIAL`) over UUIDs because it provides native, sequential ID generation without relying on PostgreSQL extensions (like `uuid-ossp`). It also offers much better index locality and lower storage overhead (4 bytes vs 16 bytes per row), which is optimal for our single-node architecture where distributed ID generation (the main advantage of UUIDs) is unnecessary.
  - **Decision:** `queries.py`'s `execute_booking` now generates `booking_id`/`payment_id` from the new `id SERIAL` sequences instead of a random `BK-XXXXXX`/`PM-XXXXXX` suffix. New helper `_gen_id(cur, table, prefix)` runs `SELECT nextval(pg_get_serial_sequence(table, 'id'))` and formats e.g. `id=21 -> "BK021"`; the same reserved integer is then explicitly inserted into the row's `id` column (so `id` and the business ID stay numerically in sync) via updated `sql_insert_booking`/`sql_insert_payment` (`id` added as first column + param). Removed now-unused `random`/`string` imports and old `_gen_booking_id`/`_gen_payment_id`. **Why:** User asked for generated booking/payment IDs to follow the same zero-padded sequential convention as `user_id` and the seed data (`BK001`, `PM001`, ...) instead of an unrelated random string, now that an `id SERIAL` sequence exists to derive them from.
  - **Decision:** Soft Delete via `deleted_at TIMESTAMP`. **Why:** Required by business rules.
  - **Decision:** `user_credentials` table decoupled from `users` with `UNIQUE(user_id)`. **Why:** Better security isolation, compliant with rules, and enforces one credential record per user so credential seeding is idempotent.
  - **Decision:** Removed explicit `salt` column from `user_credentials`. **Why:** We are using `argon2id` which automatically generates a CSPRNG salt and embeds it directly in the hash string (MCF format). A separate salt column is redundant and unused.
  - **Decision:** Normalized `stops_in_order` and `travel_time_from_origin_min` into junction tables (`metro_schedule_stops`, `national_rail_schedule_stops`). **Why:** Strict adherence to grading criteria normalization requirements and direct support for route-order queries. `JSONB` is kept only for small schedule-attached or document-like structures that are not queried as independent entities, including `operates_on`, `fare_classes`, `passed_through_stations`, and `coaches`.
  - **Decision:** Seed normal national rail services with `passed_through_stations = NULL`. **Why:** The field only applies to express services that pass through stations without stopping; for normal services it is not applicable.
  - **Decision:** Use `TIMESTAMPTZ` for all datetimes. **Why:** Required by grading criteria.
  - **Decision:** Added `UNIQUE(station_id, line)` to station_lines tables and explicitly defined `ON DELETE` behavior. **Why:** To ensure seeding idempotency and referential integrity.
  - **Decision:** Separate nullable FKs for polymorphic relationship (`payments` and `feedback`). **Why:** Allows DB to enforce referential integrity.
  - **Decision:** Added `UNIQUE` to both FK columns of `feedback` and changed `execute_submit_feedback` to upsert (`ON CONFLICT … DO UPDATE`). **Why:** Without UNIQUE, a repeat `submit_feedback` call (e.g. user wanting to change their rating) inserted a second row for the same booking, leaving two conflicting feedback records. Upsert makes the tool idempotent — feedback cardinality is now 0..1 per booking/trip (updated in ERD and Section 1 notes). Requires `docker compose down -v` + full re-seed after pulling.
  - **Decision:** Hardened the polymorphic FK pattern with DB-level constraints: `CHECK (num_nonnulls(national_rail_booking_id, metro_trip_id) = 1)` on both `payments` and `feedback` (exactly one target, never both/neither), plus `UNIQUE` on each payments FK (at most one payment per booking/trip; PostgreSQL UNIQUE ignores NULLs). `feedback` deliberately has no FK UNIQUE — a booking may receive multiple feedback rows. **Why:** These rules were previously enforced only by the `execute_booking`/seeder write paths; moving them into the schema makes them hold for any writer. Side effect: combined with `ON DELETE SET NULL`, a hard DELETE of a referenced booking/trip is now blocked by the CHECK — intended, since the business rule mandates soft delete and financial records must not be orphaned. Verified `payments.json` (40 rows) has no duplicate `booking_id` and `feedback.json` has no missing `booking_id`, so seeding is unaffected. Requires `docker compose down -v` + full re-seed after pulling.
  - **Decision:** Changed `date_of_birth DATE` to `year_of_birth SMALLINT` in `users` table. **Why:** The registration form (`register_user`) only collects year of birth; storing a full DATE would require fabricating month/day, which is semantically incorrect. Seeding extracts the year from the mock data's full date string.
- [x] Agent modifications (Task 6 Extension):
  - **Decision:** Modified `skeleton/agent.py` to add `get_departure_times` tool and `departure_time` parameter to `make_booking`. **Why:** The original agent had no way to show users available train departure times or pass a selected time to `execute_booking`. Without this, `departure_time` in `national_rail_bookings` could only be filled with `first_train_time` (inaccurate). The extension adds `query_departure_times` to `queries.py` and wires it through the agent so users can select the exact train they want before booking.
  - **Decision:** `query_departure_times` accepts an optional `boarding_station_id`. When provided, each result also includes `estimated_arrival_at_boarding_station` (computed as `departure_time + travel_time_from_origin_min` for that stop) plus a `note` explicitly labeling it as an ESTIMATE. **Why:** `departure_time` represents when the train leaves the schedule's origin station, not when it reaches the user's boarding station — without this, a user boarding mid-route would be shown a misleading time. The agent's `get_departure_times` tool description instructs the LLM to present this value as an estimate.
  - **Decision:** Each `schedule_id` actually represents many independent daily departures (`first_train_time` + N × `frequency_min`, up to `last_train_time`), each with its own seat pool — but seat-occupancy logic was previously scoped only to `(schedule_id, travel_date)`, treating all departures of a day as one shared pool. Fixed across `queries.py`: `execute_booking`'s seat-conflict check and auto-assign query (`sql_available_seat`) now also filter on `departure_time`; `query_available_seats` and `query_national_rail_availability` gained an optional `departure_time` parameter that scopes their seat-availability counts to one specific departure (omitting it falls back to the old, less-precise schedule+date-wide approximation). **Why:** Without this, two different trains running on the same schedule_id/date could incorrectly be treated as fighting over the same seats, and `available_seats` counts could under-report capacity.
  - **Decision:** `execute_booking` now validates `departure_time` (if supplied) against the schedule's actual timetable: must be `>= first_train_time` and `<= last_train_time`, and `(departure_time - first_train_time) % frequency_min == 0` (i.e. must be a time `query_departure_times` would generate). Also validates the requested `travel_date`'s day-of-week is in `operates_on`. Both checks return `False` with an explanatory error message if violated. **Why:** Previously any string could be passed as `departure_time`, allowing bookings for trains that don't actually run (wrong time-of-day or wrong day of week), which would corrupt the per-departure seat-pool logic above.
  - **Decision:** Propagated optional `departure_time` parameter to `agent.py`'s `check_national_rail_availability` and `get_available_seats` tool definitions (TOOLS list + TOOLS_SCHEMA), with descriptions explaining it scopes `available_seats`/seat results to one specific train (selected via `get_departure_times`). `_execute_tool` dispatcher needed no change since both calls already use `**params` passthrough. **Why:** Required so the LLM can actually supply the new parameter; otherwise the `queries.py` fix would be unreachable from the agent.
  - **Decision:** Added an explicit instruction block to the final-answer prompt (Step 3, when `get_user_bookings` is among the tool results) telling the LLM to keep `national_rail` and `metro` results in separate groups with their own fields, never invent/null-fill missing fields by merging the two schemas, and never claim booking history is viewable without login. **Why:** Small Ollama models (e.g. llama3.2:1b) were observed merging the two differently-shaped lists into one, fabricating a fake booking entry full of `None`s for the metro trip, and incorrectly stating no login was required — even though `query_user_bookings`/`_execute_tool` already correctly require a logged-in user and return correctly separated, correctly-shaped data.
- [x] Graph schema:
  - **Decision:** Static Topology Graph with specific network labels (`:MetroStation`, `:NationalRailStation`). Removed the generic `:Station` label. **Why:** Simplifies the seed script. Both node types can easily be matched together where necessary.
  - **Decision:** Separate relationships `[:METRO_LINK]`, `[:RAIL_LINK]`, `[:INTERCHANGE_TO]`. **Why:** Optimizes Neo4j traversal based on relationship type and allows easy weighting (`travel_time_min`) for APOC Dijkstra shortest-path algorithms.
  - **Decision:** Unified `transfer_time_min` into `travel_time_min` for `[:INTERCHANGE_TO]`. **Why:** This allows `apoc.algo.allSimplePaths` and `dijkstra` to sum a single common property (`travel_time_min`) across all relationship types.
  - **Decision:** Pre-calculated fare costs are stored directly as edge weights (`cost_usd` on `METRO_LINK`, `cost_standard_usd`/`cost_first_usd` on `RAIL_LINK`). **Why:** Allows `query_cheapest_route` to find the cheapest path using Dijkstra based on precomputed cost weights instead of trying to dynamically calculate table-based fares within the graph.

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```

—————資料庫專題要注意—————

# TransitFlow 專案開發與資料庫設計規範

## 📌 一、核心設計與商業邏輯 (Business Logic & Core Design)
- **商業規則**：務必徹底確認並遵循專案的 Business Rules。
- **刪除機制**：必須利用軟刪除 (Soft Delete) 處理，嚴禁直接從資料庫實體抹除資料。
- **權限控管**：使用者在「未登入」狀態下，絕對無法撈取任何訂票紀錄。
- **主鍵設計 (PK)**：由團隊自行決定使用 **UUID v7**（建議以 `binary(16)` 儲存以節省空間）或 **Auto Increments** (自動遞增)。
- **驗收標準**：重點在於檢視「回傳內容」是否正確。若因 AI 助理能力不足導致錯誤可忽視。

## 🔒 二、資安與密碼防護 (Security & Credential Management)
- **演算法限制**：嚴禁明碼儲存。Hash 演算法禁止使用 MD5、SHA 系列，強烈建議使用 **argon2id**。
- **獨立資料表**：密碼嚴禁存放在 `user` 資料表中，必須抽離至獨立資料表（如 `user_credentials`）。
  - **欄位規範**：包含 `c_id` (Surrogate Key / 代理鍵)、`u_id` (Foreign Key / 外鍵)、hash、salt。
  - **權限控管**：必須對此表設定嚴格的存取權限，亦可考慮獨立存放在另一個 Schema。
- **Salt 生成**：可用 **CSPRNG** 生成，並於資料庫中設定妥當的字元長度。
- **驗證位置**：Hash 比對與驗證可在 Web Server 端或資料庫端執行。
- **帳號救援**：必須實作設定「秘密問題 (Secret Question)」與答案的比對機制。

## 📁 三、專案檔案配合與實作細節 (Implementation & Configurations)
- **架構優先 (Schema-First)**：先設計好資料表。務必確保 `schema.sql`、`seed_postgres.py` 與 `registered_users.json` 三者的欄位與邏輯完美配合。
- **向量資料庫 (Vector DB)**：`seed_vectors.py` 已經實作完畢，開發時無需修改（目前架構為一個文件對應一個向量）。
- **逾時設定 (Timeout)**：系統預設為 300 秒，如需修改可至 `skeleton/config.py` 中調整。
- **開放空間**：`feedback.json` 目前尚未有具體的實作規範，由團隊自行決定如何應用與發揮。
