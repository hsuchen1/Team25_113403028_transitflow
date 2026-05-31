# AI_SESSION_CONTEXT Update Recommendations

This file records recommended updates for `AI_SESSION_CONTEXT.md`.
It does not modify the shared context file directly.

## 1. Clarify JSONB Decision for Relational Schema

Status: Applied to `AI_SESSION_CONTEXT.md`.

Current decision log says:

> Normalized `stops_in_order` and `travel_time_from_origin_min` into junction tables (`metro_schedule_stops`, `national_rail_schedule_stops`). Why: Strict adherence to grading criteria normalization requirements. `JSONB` is only kept for `operates_on` and `coaches`.

Recommended update:

> Normalized `stops_in_order` and `travel_time_from_origin_min` into junction tables (`metro_schedule_stops`, `national_rail_schedule_stops`). Why: Strict adherence to grading criteria normalization requirements and to support route-order queries with simple joins. `JSONB` is kept only for small schedule-attached or document-like structures that are not queried as independent entities, including `operates_on`, `fare_classes`, `passed_through_stations`, and `coaches`.

Reason:

- `schema.sql` currently stores `fare_classes JSONB` and `passed_through_stations JSONB`.
- The existing decision log says JSONB is only kept for `operates_on` and `coaches`, which no longer matches the schema.
- `fare_classes` can reasonably remain JSONB because fare lookup is schedule-scoped: the query already has `schedule_id` and extracts one fare class from one row.
- `passed_through_stations` can reasonably remain JSONB because it records skipped stations for express services, not boardable stops used in routing or availability.

## 2. Clarify National Rail Schedule Seeding Convention

Status: Applied to `AI_SESSION_CONTEXT.md`.

Recommended addition to the team decisions or implementation notes:

> For normal national rail services, `passed_through_stations` is seeded as an empty JSON array (`[]`) instead of `NULL`. Why: in the mock data, the missing field means there are no skipped stations, not that the value is unknown. This keeps the column consistently usable as a JSON array.

Reason:

- Normal services do not have `passed_through_stations` in the source JSON.
- Storing `[]` gives a clearer meaning: "no skipped stations".
- It avoids future query/debug logic needing to handle both `NULL` and JSON arrays.

## 3. Fix Graph Relationship Naming Inconsistency

Status: Pending. Not changed because graph-related files and graph decisions are outside the current relational task scope.

Current graph schema section says:

> `[:INTERCHANGE_TO]`

Current decision log says:

> `[:INTERCHANGE_WITH]`

Recommended update:

Choose one relationship name and use it consistently in both the graph schema section and decision log.

Recommended wording if the team keeps the graph schema section as-is:

> **Decision:** Separate relationships `[:METRO_LINK]`, `[:RAIL_LINK]`, `[:INTERCHANGE_TO]`. **Why:** Optimizes Neo4j traversal based on relationship type and allows easy weighting (`travel_time_min` or `transfer_time_min`) for shortest-path algorithms.

Reason:

- `AI_SESSION_CONTEXT.md` currently gives conflicting graph relationship names.
- Future AI-generated Cypher may use the wrong relationship type if this remains inconsistent.

## 4. Optional: Add Schedule Seeder Implementation Notes

Recommended addition:

> PostgreSQL seeding now loads schedules in two layers: schedule header rows go into `metro_schedules` / `national_rail_schedules`, while stopping sequences go into `metro_schedule_stops` / `national_rail_schedule_stops`. Seed order must be stations -> schedules -> schedule stops because schedule stop rows depend on both station and schedule foreign keys.

Reason:

- This documents the dependency order used by `seed_postgres.py`.
- It helps future AI sessions avoid reintroducing `stops_in_order` JSONB into schedule tables.
