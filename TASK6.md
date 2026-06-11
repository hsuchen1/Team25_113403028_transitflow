# Task 6 Extension

This file lists all the files and database components that were modified or added to implement the Task 6 optional extension. 
All files listed below also include a `# TASK 6 EXTENSION:` comment near the top to facilitate TA grading.

## 1. Modified Files

- `databases/relational/queries.py`: Added `query_departure_times`, scoped seat-availability/booking logic to a specific `departure_time` (instead of treating a whole day's schedule as one shared seat pool), and added timetable/`operates_on` validation in `execute_booking`. Added `execute_submit_feedback` to handle writing user ratings and comments to the database.
- `databases/graph/queries.py`: Extended shortest path and route-finding queries to support complex multi-hop pathfinding requested by the agent.
- `skeleton/agent.py`: Integrated new agent tools (`get_departure_times`, `find_all_paths`, `get_station_connections`, `submit_feedback`), propagated the new optional `departure_time` parameter through `make_booking`, `check_national_rail_availability`, and `get_available_seats`, and added a final-answer prompt instruction that keeps `national_rail`/`metro` booking history in separate, correctly-shaped groups.

## 2. Specific Function and Table Modifications

### Relational Database
- **New/Modified Functions**: 
  - `query_departure_times(schedule_id, boarding_station_id?)`: Generates the actual list of daily departures (`first_train_time` + N × `frequency_min`, up to `last_train_time`) for a schedule. If `boarding_station_id` is given, also returns an estimated `estimated_arrival_at_boarding_station` for that stop, clearly labeled as an ESTIMATE.
  - `execute_booking()`: Two separate Task 6 changes:
    1. **Per-departure seat pool fix** — the seat-conflict check and auto-assign query (`sql_available_seat`) now filter on `departure_time` as well as `schedule_id`/`travel_date`, so different trains on the same schedule no longer compete for the same seats.
    2. **Timetable validation** — if `departure_time` is supplied, it must fall within `[first_train_time, last_train_time]` and align with `frequency_min`; the requested `travel_date`'s day-of-week must also be in `operates_on`. Either check failing returns `(False, message)`.
  - `query_available_seats()` & `query_national_rail_availability()`: Now optionally accept `departure_time` to check capacity on one specific train instead of a broad schedule+date-wide approximation.
  - `execute_submit_feedback(user_id, booking_id, rating, comment)`: Writes user ratings and comments to the `feedback` table, automatically interpreting whether the `booking_id` represents a National Rail booking or Metro trip based on its prefix.
- **Affected Tables**: 
  - Integrates tightly with `national_rail_bookings`, `national_rail_schedules`, and `national_rail_schedule_stops`.
  - Added new integration for the `feedback` table.

### Graph Database
- **New/Modified Functions**:
  - `query_all_paths_between()` / `find_all_paths()`: Extended Neo4j traversal limiting results to top N paths to prevent node explosion, fulfilling complex multi-network journey planning.


### Agent Logic
- **New Tools**: `get_departure_times`, `find_all_paths`, `get_station_connections`, `submit_feedback`.
- **New/changed parameters**: Added optional `departure_time` to `make_booking`, `check_national_rail_availability`, and `get_available_seats` (TOOLS list + TOOLS_SCHEMA), so the LLM can pass a specific train selected via `get_departure_times` through to the relational queries above.
- **Booking history prompt fix**: Added an explicit instruction block to the Step 3 final-answer prompt (used when `get_user_bookings` is among the tool results) telling the LLM to keep `national_rail` and `metro` results in separate groups with their own fields, never invent/null-fill missing fields by merging the two schemas, and never claim booking history is viewable without login. Added because small Ollama models (e.g. llama3.2:1b) were observed merging the two differently-shaped lists and fabricating fields.
