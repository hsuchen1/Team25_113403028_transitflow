# Task 6 Extension

This file lists all the files and database components that were modified or added to implement the Task 6 optional extension. 
All files listed below also include a `# TASK 6 EXTENSION:` comment near the top to facilitate TA grading.

## 1. Modified Files

- `databases/relational/queries.py`: Added new queries (e.g., `query_departure_times`) to support advanced seat booking mechanics and precise train departure time selection.
- `databases/graph/queries.py`: Extended shortest path and route-finding queries to support complex multi-hop pathfinding requested by the agent.
- `skeleton/agent.py`: Integrated new agent tools (`get_departure_times`, `find_all_paths`, `get_station_connections`) and updated the agent's LLM prompt logic to accurately parse the user's booking intentions based on specific departure times.

## 2. Specific Function and Table Modifications

### Relational Database
- **New/Modified Functions**: 
  - `query_departure_times()`: Resolves station connectivity into actual departure timetables to present users with precise train times.
  - `execute_booking()`: Added validation and accurate logic to account for specific `departure_time` (rather than just treating the whole day's schedule as one seat pool).
  - `query_available_seats()` & `query_national_rail_availability()`: Now optionally accept `departure_time` to check capacity on specific trains instead of a broad daily approximation.
- **Affected Tables**: 
  - Integrates tightly with `national_rail_bookings`, `national_rail_schedules`, and `national_rail_schedule_stops`.

### Graph Database
- **New/Modified Functions**:
  - `query_all_paths_between()` / `find_all_paths()`: Extended Neo4j traversal limiting results to top N paths to prevent node explosion, fulfilling complex multi-network journey planning.
  - `query_station_connections()`: Returns direct neighbors and travel time metadata from the Graph.

### Agent Logic
- **New Tools**: `get_departure_times`, `find_all_paths`, `get_station_connections`.
- **Logic**: Strict parameter checking in `make_booking` and strict schema separation between `metro` and `national_rail` in booking history formats to prevent LLM hallucinations.
