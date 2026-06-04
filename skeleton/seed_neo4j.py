import os
import json
from neo4j import GraphDatabase
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
MOCK_DIR    = os.path.join(SCRIPT_DIR, '..', 'train-mock-data')

def _load(filename):
    with open(os.path.join(MOCK_DIR, filename), 'r', encoding='utf-8') as f:
        return json.load(f)

def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")
    metro_schedules = _load("metro_schedules.json")
    rail_schedules = _load("national_rail_schedules.json")

    # Build cost mappings for metro (from_station, to_station) -> cost_usd
    metro_costs = {}
    for schedule in metro_schedules:
        stops = schedule.get("stops_in_order", [])
        base_fare = schedule.get("base_fare_usd", 0)
        per_stop_rate = schedule.get("per_stop_rate_usd", 0)
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                from_station = stops[i]
                to_station = stops[j]
                stops_travelled = j - i
                cost = base_fare + (stops_travelled - 1) * per_stop_rate
                key = (from_station, to_station)
                if key not in metro_costs or cost < metro_costs[key]:
                    metro_costs[key] = cost

    # Build cost mappings for national rail (from_station, to_station) -> {standard, first}
    rail_costs = {}
    for schedule in rail_schedules:
        stops = schedule.get("stops_in_order", [])
        fare_classes = schedule.get("fare_classes", {})
        if not fare_classes:
            continue
            
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                from_station = stops[i]
                to_station = stops[j]
                stops_travelled = j - i
                standard_cost = fare_classes["standard"]["base_fare_usd"] + (stops_travelled - 1) * fare_classes["standard"]["per_stop_rate_usd"]
                first_cost = fare_classes["first"]["base_fare_usd"] + (stops_travelled - 1) * fare_classes["first"]["per_stop_rate_usd"]
                key = (from_station, to_station)
                if key not in rail_costs:
                    rail_costs[key] = {"standard": standard_cost, "first": first_cost}
                else:
                    rail_costs[key]["standard"] = min(rail_costs[key]["standard"], standard_cost)
                    rail_costs[key]["first"] = min(rail_costs[key]["first"], first_cost)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # Create MetroStation nodes (using MERGE for idempotency)
        for station in metro_stations:
            session.run(
                """
                MERGE (n:MetroStation {station_id: $station_id})
                SET n.name = $name,
                    n.lines = $lines,
                    n.is_interchange_metro = $is_interchange_metro,
                    n.is_interchange_national_rail = $is_interchange_national_rail
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                is_interchange_metro=station["is_interchange_metro"],
                is_interchange_national_rail=station["is_interchange_national_rail"]
            )
        print(f"  Created {len(metro_stations)} MetroStation nodes")

        # Create NationalRailStation nodes (using MERGE for idempotency)
        for station in rail_stations:
            session.run(
                """
                MERGE (n:NationalRailStation {station_id: $station_id})
                SET n.name = $name,
                    n.lines = $lines,
                    n.is_interchange_national_rail = $is_interchange_national_rail,
                    n.is_interchange_metro = $is_interchange_metro
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"],
                is_interchange_national_rail=station["is_interchange_national_rail"],
                is_interchange_metro=station["is_interchange_metro"]
            )
        print(f"  Created {len(rail_stations)} NationalRailStation nodes")

        # Create METRO_LINK relationships for metro stations
        metro_links_count = 0
        for station in metro_stations:
            for adjacent in station.get("adjacent_stations", []):
                from_id = station["station_id"]
                to_id = adjacent["station_id"]
                cost_usd = metro_costs.get((from_id, to_id), 1.10)
                session.run(
                    """
                    MATCH (from:MetroStation {station_id: $from_id})
                    MATCH (to:MetroStation {station_id: $to_id})
                    MERGE (from)-[r:METRO_LINK {line: $line, travel_time_min: $travel_time_min}]->(to)
                    SET r.cost_usd = $cost_usd
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                    cost_usd=cost_usd
                )
                metro_links_count += 1
        print(f"  Created {metro_links_count} METRO_LINK relationships")

        # Create RAIL_LINK relationships for national rail stations
        rail_links_count = 0
        for station in rail_stations:
            for adjacent in station.get("adjacent_stations", []):
                from_id = station["station_id"]
                to_id = adjacent["station_id"]
                costs = rail_costs.get((from_id, to_id), {"standard": 4.00, "first": 6.50})
                session.run(
                    """
                    MATCH (from:NationalRailStation {station_id: $from_id})
                    MATCH (to:NationalRailStation {station_id: $to_id})
                    MERGE (from)-[r:RAIL_LINK {line: $line, travel_time_min: $travel_time_min}]->(to)
                    SET r.cost_standard_usd = $cost_standard,
                        r.cost_first_usd = $cost_first
                    """,
                    from_id=from_id,
                    to_id=to_id,
                    line=adjacent["line"],
                    travel_time_min=adjacent["travel_time_min"],
                    cost_standard=costs["standard"],
                    cost_first=costs["first"]
                )
                rail_links_count += 1
        print(f"  Created {rail_links_count} RAIL_LINK relationships")

        # Create INTERCHANGE_TO relationships
        interchange_count = 0
        for station in metro_stations:
            if station.get("is_interchange_national_rail") and station.get("interchange_national_rail_station_id"):
                session.run(
                    """
                    MATCH (metro:MetroStation {station_id: $metro_id})
                    MATCH (rail:NationalRailStation {station_id: $rail_id})
                    MERGE (metro)-[:INTERCHANGE_TO {travel_time_min: 5}]->(rail)
                    MERGE (rail)-[:INTERCHANGE_TO {travel_time_min: 5}]->(metro)
                    """,
                    metro_id=station["station_id"],
                    rail_id=station["interchange_national_rail_station_id"]
                )
                interchange_count += 2
        print(f"  Created {interchange_count} INTERCHANGE_TO relationships")

    driver.close()
    print("\nNeo4j graph seeded successfully.")

if __name__ == "__main__":
    seed()
